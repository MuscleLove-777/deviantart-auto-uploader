# -*- coding: utf-8 -*-
"""
DeviantArt自動アップロード（GitHub Actions用）
Google Driveからダウンロード → ランダム1ファイルをSta.sh経由でアップロード・公開
"""
import sys, json, os, random, time

import requests
import gdown

# ============================================================
# 設定
# ============================================================

GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")
DA_CLIENT_ID = os.environ.get("DA_CLIENT_ID", "")
DA_CLIENT_SECRET = os.environ.get("DA_CLIENT_SECRET", "")
DA_ACCESS_TOKEN = os.environ.get("DA_ACCESS_TOKEN", "")
DA_REFRESH_TOKEN = os.environ.get("DA_REFRESH_TOKEN", "")

PATREON_LINK = "https://www.patreon.com/cw/MuscleLove?utm_source=deviantart"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.wmv', '.mkv', '.webm'}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
MAX_FILE_SIZE = 200 * 1024 * 1024  # DeviantArt limit: 200MB
UPLOADED_LOG = "uploaded.json"

# --- MuscleLove バックリンクプール（DeviantArt: アダルト+フィットネス両OK） ---
ML_BACKLINK_POOL = [
    ("https://musclelove-777.github.io/female-physique-queens/", "Female Physique Queens"),
    ("https://musclelove-777.github.io/muscle-meal-girls/", "Muscle Meal Girls"),
    ("https://musclelove-777.github.io/armwrestling-girls-navi/", "Armwrestling Girls Navi"),
    ("https://musclelove-777.github.io/physique-girls-navi/", "Physique Girls Navi"),
    ("https://musclelove-777.github.io/fighting-girls-navi/", "Fighting Girls Navi"),
    ("https://musclelove-777.github.io/joshi-prowrestling-navi/", "Joshi ProWrestling Navi"),
    ("https://musclelove-777.github.io/network/fitness/", "MuscleLove Fitness Network"),
    ("https://musclelove-777.github.io/network/academy/", "MuscleLove Academy 77"),
]


def build_backlink_block():
    """MuscleLove バックリンクHTMLブロック（ランダム2件、冪等マーカー付き）"""
    try:
        k = min(2, len(ML_BACKLINK_POOL))
        selected = random.sample(ML_BACKLINK_POOL, k=k)
        items = " | ".join([f'<a href="{u}">{n}</a>' for u, n in selected])
        return (
            "<br/>"
            "<!-- ML_BACKLINK -->"
            f"🔗 Related: {items}"
            "<!-- /ML_BACKLINK -->"
        )
    except Exception:
        return ""

# フォルダ名・ファイル名からコンテンツを推測してタグを生成するマッピング
CONTENT_TAG_MAP = {
    'training': ['筋トレ', 'workout', 'training', 'gym', 'fitness'],
    'workout': ['筋トレ', 'workout', 'training', 'gym', 'fitness'],
    'toilet': ['筋肉女子', 'muscle girl', 'muscular woman'],
    'pullups': ['懸垂', 'pullups', 'pull ups', 'back workout', 'calisthenics'],
    'posing': ['ポージング', 'posing', 'bodybuilding', 'physique'],
    'flex': ['フレックス', 'flex', 'muscle', 'bodybuilding'],
    'muscle': ['筋肉', 'muscle', 'muscular', 'fitness'],
    'bicep': ['上腕二頭筋', 'biceps', 'arms', 'muscle'],
    'abs': ['腹筋', 'abs', 'sixpack', 'core'],
    'leg': ['脚トレ', 'legs', 'quads', 'legday'],
    'back': ['背中', 'back', 'lats', 'backday'],
    'squat': ['スクワット', 'squat', 'legs', 'legday'],
    'deadlift': ['デッドリフト', 'deadlift', 'powerlifting'],
    'bench': ['ベンチプレス', 'benchpress', 'chest'],
}

# 常に付与するベースタグ
BASE_TAGS = [
    'fit', 'strongwomen', 'strongbody', 'strong', 'shreddedgirls', 'shredded',
    'nofilter', 'noedits', 'naturalmuscle', 'muscles', 'musclegirl', 'hardbodies',
    'girlswithmuscles', 'fitnessbody', 'fitnation', 'fitmodel', 'fitfam',
    'athleticgirl', 'athletic', 'bikini', 'girlswithabs', 'girlswholift',
    'ripped', 'muscle', 'armpit', 'gyaru', 'MuscleLove',
    'musclebeauty', 'thicc', 'thickfit', 'armpitfetish', 'tonedbody',
    'fitchick', 'muscleworship',
]

# タイトル候補（ランダムに選択）
TITLE_TEMPLATES = [
    # Power/strength themed (8)
    "Forged in Iron 🔥",
    "Crush Everything 💪",
    "Titan Mode Activated ✨",
    "Wrecking Ball Energy 💥",
    "Powerhouse Unleashed 🔥",
    "Breaking Limits Daily 💪",
    "War Machine Physique ✨",
    "Apex Predator Gains 🔥",
    # Aesthetic/beauty themed (8)
    "Sculpted Elegance ✨",
    "Marble & Muscle 🔥",
    "Goddess Tier Physique 💪",
    "Velvet Over Steel ✨",
    "Art in Motion 🔥",
    "Symmetry Perfection 💪",
    "Divine Proportions ✨",
    "Living Sculpture 🔥",
    # Provocative/edgy (5)
    "Try to Look Away 👀",
    "Not Your Average Girl 💪",
    "Handle With Caution 🔥",
    "Dangerously Thick ✨",
    "Too Strong to Ignore 💥",
    # Japanese-English mix (4)
    "筋肉美 Muscle Art ✨",
    "鋼の女 Iron Woman 🔥",
    "最強ボディ Ultimate 💪",
    "筋トレ女神 Gym Deity ✨",
    # --- Pool expansion 2026-04-25 (was 25 titles → 35, +40%) ---
    # Time-of-day flavored
    "Sunrise Pump Ritual 🌅",
    "Midnight Iron Session 🌙",
    "Golden Hour Physique ✨",
    # Seasonal hint
    "Summer Shred Mode 🔥",
    "Winter Bulk Goddess ❄",
    # Aesthetic / mythic
    "Olympus Tier Aesthetics 💪",
    "Bronze Age Sculpture 🔥",
    "Quantum Gains Activated ✨",
    # JP mix expansion
    "極限フィジーク Apex 💪",
    "黄金比 Golden Ratio ✨",
]


# ============================================================
# トークン管理
# ============================================================

def refresh_access_token(access_token, refresh_token):
    """refresh_tokenを使ってaccess_tokenを更新する"""
    if not refresh_token:
        print("Error: No refresh_token available.")
        return access_token, refresh_token

    print("Refreshing access token...")
    r = requests.post("https://www.deviantart.com/oauth2/token", data={
        'grant_type': 'refresh_token',
        'client_id': DA_CLIENT_ID,
        'client_secret': DA_CLIENT_SECRET,
        'refresh_token': refresh_token,
    })

    if r.status_code != 200:
        print(f"Token refresh failed: {r.status_code} {r.text}")
        return access_token, refresh_token

    token_data = r.json()
    if 'access_token' not in token_data:
        print(f"Token refresh failed: {token_data}")
        return access_token, refresh_token

    new_access_token = token_data['access_token']
    new_refresh_token = token_data.get('refresh_token', refresh_token)

    print("Token refresh successful!")
    # Print new tokens so they can be captured in workflow logs for secret updates
    print(f"::notice::NEW_ACCESS_TOKEN={new_access_token}")
    print(f"::notice::NEW_REFRESH_TOKEN={new_refresh_token}")

    return new_access_token, new_refresh_token


def get_valid_token(access_token, refresh_token):
    """有効なaccess_tokenを取得する（必要なら自動リフレッシュ）"""
    if not access_token:
        return refresh_access_token(access_token, refresh_token)

    # トークンの有効性をチェック
    r = requests.get("https://www.deviantart.com/api/v1/oauth2/user/whoami",
                      params={'access_token': access_token})
    if r.status_code == 200:
        user = r.json().get('username', 'unknown')
        print(f"Auth OK: {user}")
        return access_token, refresh_token
    else:
        print(f"Token expired (status={r.status_code}), refreshing...")
        return refresh_access_token(access_token, refresh_token)


# ============================================================
# アップロード済み管理
# ============================================================

def load_uploaded_log():
    """アップロード済みファイルの記録を読み込む"""
    if not os.path.exists(UPLOADED_LOG):
        return {"files": [], "tokens": {}}
    with open(UPLOADED_LOG, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Support both old list format and new dict format
    if isinstance(data, list):
        return {"files": data, "tokens": {}}
    return data


def save_uploaded_log(log_data):
    """アップロード済みファイルの記録を保存する"""
    with open(UPLOADED_LOG, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


# ============================================================
# Google Driveダウンロード
# ============================================================

def download_media():
    """Google Driveフォルダからメディアファイルをダウンロードする"""
    dl_dir = "media"
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    print(f"Downloading from Google Drive: {url}")
    try:
        gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
    except Exception as e:
        print(f"Download error: {e}")

    files = []
    for root, dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in ALL_EXTENSIONS:
                size = os.path.getsize(fpath)
                if size <= MAX_FILE_SIZE:
                    files.append(fpath)
                else:
                    print(f"Skip (>200MB): {fname} ({size / 1024 / 1024:.1f}MB)")
    return files


# ============================================================
# タグ・説明文生成
# ============================================================

def generate_tags(file_path):
    """フォルダ名・ファイル名からコンテンツを推測してタグを生成"""
    tags = list(BASE_TAGS)

    path_lower = file_path.lower().replace('\\', '/').replace('-', ' ').replace('_', ' ')

    matched = set()
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in path_lower:
            for t in keyword_tags:
                if t not in matched:
                    tags.append(t)
                    matched.add(t)

    # 重複除去しつつ順序保持
    seen = set()
    unique_tags = []
    for t in tags:
        t_lower = t.lower()
        if t_lower not in seen:
            seen.add(t_lower)
            unique_tags.append(t)

    return unique_tags


def sanitize_category(name, max_len=30):
    """フォルダ名からカテゴリ名を安全に抽出する（プロンプト文字列を除去）"""
    import re
    # 中括弧やプロンプト記法を除去
    name = re.sub(r'[{}\[\]]', '', name)
    # カンマ区切りの長いプロンプト文字列は最初の部分だけ使う
    if ',' in name:
        name = name.split(',')[0].strip()
    # 先頭・末尾の空白やハイフンを除去
    name = name.strip(' -_')
    # 長すぎる場合は切り詰め
    if len(name) > max_len:
        name = name[:max_len].rstrip(' -_')
    return name if name else "Muscle"


def build_description(file_path, tags):
    """Patreonリンク付き説明文を生成"""
    parts = file_path.replace('\\', '/').split('/')
    category = "Muscle"
    for p in parts:
        if p not in ['media', ''] and '.' not in p:
            category = sanitize_category(p)
            break

    hashtags = ' '.join([f'#{t.replace(" ", "")}' for t in tags[:15]])

    description = f'🔥 More content on Patreon → <a href="{PATREON_LINK}">MuscleLove</a>'
    description = description + " " + build_backlink_block()

    return category, description


# ============================================================
# DeviantArt API アップロード
# ============================================================

def upload_to_stash(access_token, file_path, title, tags, artist_comments):
    """Sta.shにファイルをアップロードする"""
    fname = os.path.basename(file_path)
    size_mb = os.path.getsize(file_path) / 1024 / 1024
    print(f"\nUploading to Sta.sh: {fname} ({size_mb:.1f}MB)")

    url = "https://www.deviantart.com/api/v1/oauth2/stash/submit"

    with open(file_path, 'rb') as f:
        files = {
            'file': (fname, f),
        }
        data = [
            ('access_token', access_token),
            ('title', title),
            ('artist_comments', artist_comments),
            ('is_mature', 'true'),
            ('is_ai_generated', 'true'),
        ]
        for t in tags[:30]:
            data.append(('tags[]', t))

        r = requests.post(url, data=data, files=files, timeout=600)

    if r.status_code != 200:
        print(f"Sta.sh upload failed: {r.status_code}")
        try:
            err = r.json()
            print(f"  Error: {err}")
            if err.get('error') == 'invalid_token':
                return None, 'token_expired'
        except Exception:
            print(f"  Response: {r.text[:500]}")
        return None, 'error'

    result = r.json()
    if result.get('status') == 'success':
        itemid = result.get('itemid')
        print(f"Sta.sh upload success! itemid: {itemid}")
        return itemid, 'success'
    else:
        print(f"Sta.sh upload failed: {result}")
        return None, 'error'


def publish_from_stash(access_token, itemid, is_mature=True):
    """Sta.shからDeviantArtに公開する"""
    print(f"\nPublishing from Sta.sh (itemid: {itemid})...")

    url = "https://www.deviantart.com/api/v1/oauth2/stash/publish"

    data = {
        'access_token': access_token,
        'itemid': itemid,
        'is_mature': 'true' if is_mature else 'false',
    }

    r = requests.post(url, data=data, timeout=120)

    if r.status_code != 200:
        print(f"Publish failed: {r.status_code}")
        try:
            err = r.json()
            print(f"  Error: {err}")
        except Exception:
            print(f"  Response: {r.text[:500]}")
        return None

    result = r.json()
    if result.get('status') == 'success':
        pub_url = result.get('url', '')
        deviationid = result.get('deviationid', '')
        print(f"Publish success!")
        if pub_url:
            print(f"  URL: {pub_url}")
        if deviationid:
            print(f"  Deviation ID: {deviationid}")
        return result
    else:
        print(f"Publish failed: {result}")
        return None


# ============================================================
# メイン
# ============================================================

def main():
    print("=== DeviantArt Auto Uploader (GitHub Actions) ===\n")

    if not all([DA_CLIENT_ID, DA_CLIENT_SECRET, GDRIVE_FOLDER_ID]):
        print("Error: Missing required environment variables")
        print("Required: DA_CLIENT_ID, DA_CLIENT_SECRET, GDRIVE_FOLDER_ID")
        print("Required: DA_ACCESS_TOKEN or DA_REFRESH_TOKEN")
        return 1

    access_token = DA_ACCESS_TOKEN
    refresh_token = DA_REFRESH_TOKEN

    if not access_token and not refresh_token:
        print("Error: Need at least DA_ACCESS_TOKEN or DA_REFRESH_TOKEN")
        return 1

    # Load log and check for saved tokens
    log_data = load_uploaded_log()
    saved_tokens = log_data.get("tokens", {})
    if saved_tokens.get("access_token"):
        print("Using saved access token from uploaded.json")
        access_token = saved_tokens["access_token"]
    if saved_tokens.get("refresh_token"):
        refresh_token = saved_tokens["refresh_token"]

    # Validate / refresh token
    access_token, refresh_token = get_valid_token(access_token, refresh_token)

    # Save refreshed tokens to uploaded.json for persistence
    log_data["tokens"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_uploaded_log(log_data)

    # Download media from Google Drive
    media_files = download_media()
    if not media_files:
        print("No media files found!")
        return 0

    # Filter out already uploaded (skip filter if UPLOAD_ALL is set)
    if os.environ.get("UPLOAD_ALL", "").lower() in ("1", "true", "yes"):
        available = media_files
        print(f"\nUPLOAD_ALL enabled: all {len(available)} files are candidates")
    else:
        uploaded_names = [entry['file'] if isinstance(entry, dict) else entry
                          for entry in log_data.get("files", [])]
        available = [f for f in media_files if os.path.basename(f) not in uploaded_names]
        if not available:
            print("All files already uploaded!")
            return 0
        print(f"\nAvailable: {len(available)} / Total: {len(media_files)}")

    # Select random file
    selected = random.choice(available)
    fname = os.path.basename(selected)
    print(f"Selected: {fname}")

    # Generate tags and description
    tags = generate_tags(selected)

    # Google Trendsからトレンドタグを追加
    from trending import get_trending_tags
    trend_tags = get_trending_tags(max_tags=5)
    if trend_tags:
        seen = {t.lower() for t in tags}
        for t in trend_tags:
            if t.lower() not in seen:
                tags.append(t)
                seen.add(t.lower())

    category, description = build_description(selected, tags)

    # タイトル：カテゴリ + ランダムテンプレート（UTF-8で最大50バイト）
    template = random.choice(TITLE_TEMPLATES)
    title = f"{category} - {template}" if category != "Muscle" else template
    if len(title.encode('utf-8')) > 50:
        title = template  # カテゴリが長すぎる場合はテンプレートのみ
    if len(title.encode('utf-8')) > 50:
        title = title[:50]
        while len(title.encode('utf-8')) > 50:
            title = title[:-1]

    print(f"Title: {title}")
    print(f"Tags: {', '.join(tags[:10])}...")
    print(f"Category: {category}")
    print(f"Mature: true")

    # Step 1: Upload to Sta.sh
    itemid, status = upload_to_stash(access_token, selected, title, tags, description)

    # Token expired -> refresh and retry
    if status == 'token_expired':
        print("\nRefreshing token and retrying...")
        access_token, refresh_token = refresh_access_token(access_token, refresh_token)
        log_data["tokens"]["access_token"] = access_token
        log_data["tokens"]["refresh_token"] = refresh_token
        save_uploaded_log(log_data)
        itemid, status = upload_to_stash(access_token, selected, title, tags, description)

    if not itemid:
        print("Upload failed!")
        return 1

    # Step 2: Publish from Sta.sh
    result = publish_from_stash(access_token, itemid, is_mature=True)

    publish_url = ''
    if result:
        publish_url = result.get('url', '')
    else:
        print("Warning: Uploaded to Sta.sh but publish failed.")
        print(f"  Manually publish at: https://sta.sh (itemid: {itemid})")
        publish_url = '(publish_failed)'

    # Record uploaded file
    log_data["files"].append({
        'file': fname,
        'stash_itemid': itemid,
        'publish_url': publish_url,
        'uploaded_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    save_uploaded_log(log_data)

    remaining = len(available) - 1
    print(f"\nDone! Remaining: {remaining}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
