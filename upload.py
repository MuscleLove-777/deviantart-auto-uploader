# -*- coding: utf-8 -*-
"""
DeviantArt自動アップロード（GitHub Actions用）
Google Driveからダウンロード → ランダム1ファイルをSta.sh経由でアップロード・公開
"""
import sys, json, os, random, time

import requests
import gdown

# 変種バンディット（重み付き抽選＋投稿ログ）。無くても一様ランダムで動く。
try:
    from variant_bandit import pick as bandit_pick, with_utm_content, log_post
except Exception:
    def bandit_pick(kind, options, rng=random):
        o = rng.choice(options)
        return o, ""
    def with_utm_content(url, key):
        return url
    def log_post(platform, record):
        pass

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
TOKENS_FILE = "tokens.json"  # Secrets更新用（artifactにはアップロードしない・gitignore対象）
STATUS_FILE = "run_status.txt"  # ワークフローのLINE通知が読む実行結果（posted/no_content/no_media/error）


def write_status(status, remaining=-1):
    """実行結果をワークフロー通知用に書き出す。「成功=投稿された」とは限らない
    （在庫切れでもexit 0のため）ので、通知文の出し分けはこのファイルで行う。"""
    try:
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            f.write(f"status={status}\nremaining={remaining}\n")
    except Exception:
        pass

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


def build_backlink_block(variant_key=""):
    """MuscleLove バックリンク（ランダム2件）
    注意: DeviantArtはstash/publish時に説明文をtiptap形式へ変換し、
    <a>タグ・<br/>・HTMLコメントを全て剥がす（リンク情報が消える）。
    そのため素のURLテキストで記載する。utm付きなら手動コピー流入も変種単位で測れる。
    """
    try:
        k = min(2, len(ML_BACKLINK_POOL))
        selected = random.sample(ML_BACKLINK_POOL, k=k)
        def _track(u):
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}utm_source=deviantart&utm_medium=autopost"
            return with_utm_content(u, variant_key)
        items = " | ".join([f"{n} → {_track(u)}" for u, n in selected])
        return f"🔗 Related: {items}"
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
    # セキュリティ: トークン値はログに出力しない（publicリポジトリのログは誰でも閲覧可能）

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
        return {"files": []}
    with open(UPLOADED_LOG, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Support both old list format and new dict format
    if isinstance(data, list):
        return {"files": data}
    # セキュリティ: トークンはartifactに残さない（旧形式のtokensキーを除去）
    data.pop("tokens", None)
    return data


def save_tokens_file(access_token, refresh_token):
    """ワークフローのSecrets更新ステップ用にトークンを書き出す（artifact対象外）"""
    with open(TOKENS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        }, f, indent=2)


def save_uploaded_log(log_data):
    """アップロード済みファイルの記録を保存する"""
    with open(UPLOADED_LOG, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


# ============================================================
# Google Driveダウンロード
# ============================================================

def download_media():
    """Google Driveフォルダ（= refill_deviantart_queue.py が補充するキュー）から
    メディアファイルをダウンロードする。

    GDRIVE_FOLDER_ID には大元プール(mldrive:DeviantArt, 18,000枚超)ではなく
    小さなキュー(_deviantart_queue)を指定すること。大元を直接指すと Google のレート制限で
    gdown が途中停止し、毎回同じ先頭数十枚しか取れず「在庫切れ」と誤判定する
    （2026-07-19 の全ジョブ失敗の原因）。
    """
    dl_dir = "media"
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    print(f"Downloading from Google Drive: {url}")
    downloaded = None
    try:
        downloaded = gdown.download_folder(url, output=dl_dir, quiet=False)
    except Exception as e:
        # 握り潰すと「取得できた分だけ」で在庫判定してしまうため必ず可視化する
        print(f"::warning::Drive download error (部分取得の可能性): {e}")
    if downloaded is not None:
        print(f"gdown downloaded: {len(downloaded)} files")

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


def build_description(file_path, tags, variant_key=""):
    """Patreonリンク付き説明文を生成"""
    parts = file_path.replace('\\', '/').split('/')
    category = "Muscle"
    for p in parts:
        if p not in ['media', ''] and '.' not in p:
            category = sanitize_category(p)
            break

    hashtags = ' '.join([f'#{t.replace(" ", "")}' for t in tags[:15]])

    # DAはHTMLアンカーを剥がすため、素のURLをそのまま記載する
    patreon_link = with_utm_content(PATREON_LINK, variant_key)
    description = f'🔥 More content on Patreon → {patreon_link}'
    backlinks = build_backlink_block(variant_key)
    if backlinks:
        description = description + "\n\n" + backlinks

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


# 注: DeviantArtのAPIは説明文・コメントの両方でリンクを剥がす（tiptap変換で
# <a>タグ削除・素URLも非リンク化、2026-06-11に実機で確定）。よってクリック可能な
# Patreon導線はAPI経由では作れない。プロフィールのソーシャルリンクで確保すること。


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

    # Load uploaded log (dedup list)
    log_data = load_uploaded_log()

    # Validate / refresh token
    access_token, refresh_token = get_valid_token(access_token, refresh_token)

    # Secrets更新ステップ用にトークンを書き出す（uploaded.json/artifactには含めない）
    save_tokens_file(access_token, refresh_token)
    save_uploaded_log(log_data)

    # Download media from Google Drive
    media_files = download_media()
    if not media_files:
        print("No media files found!")
        write_status("no_media")
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
            write_status("no_content", 0)
            return 0
        print(f"\nAvailable: {len(available)} / Total: {len(media_files)}")

    # Select random file
    selected = random.choice(available)
    fname = os.path.basename(selected)
    print(f"Selected: {fname}")

    # Generate tags and description
    tags = generate_tags(selected)

    # content_pool（autonomyが毎日最適化）から mature レーンのタグ/NG語を取り込む
    try:
        from pool_loader import as_insights
        ins = as_insights("mature_muscle", platform="deviantart")
        seen = {t.lower() for t in tags}
        for t in ins.get("recommended_tags", []):
            if t.lower() not in seen:
                tags.append(t)
                seen.add(t.lower())
        avoid = {a.lower() for a in ins.get("avoid_tags", [])}
        if avoid:
            tags = [t for t in tags if t.lower() not in avoid]
    except Exception as e:
        print(f"pool_loader skipped: {e}")

    # Google Trendsからトレンドタグを追加
    from trending import get_trending_tags
    trend_tags = get_trending_tags(max_tags=5)
    if trend_tags:
        seen = {t.lower() for t in tags}
        for t in trend_tags:
            if t.lower() not in seen:
                tags.append(t)
                seen.add(t.lower())

    # タイトル：バンディット抽選（反応の良いタイトル傾向が自動で増える）
    template, title_vid = bandit_pick("deviantart.title", TITLE_TEMPLATES)
    variant_key = f"ti{title_vid}" if title_vid else ""
    print(f"Title variant: {title_vid or '(uniform)'}")

    category, description = build_description(selected, tags, variant_key)

    # UTF-8で最大50バイト
    title = template
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
        save_tokens_file(access_token, refresh_token)
        itemid, status = upload_to_stash(access_token, selected, title, tags, description)

    if not itemid:
        print("Upload failed!")
        write_status("error")
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

    # 変種ログ（posted_log.json、gitで永続化 → autonomyが反応と結合して重み更新）
    deviationid = (result or {}).get('deviationid', '')
    log_post("deviantart", {
        "deviationid": str(deviationid),
        "publish_url": publish_url,
        "file": fname,
        "variants": {"deviantart.title": title_vid},
        "tags_count": len(tags),
    })

    remaining = len(available) - 1
    write_status("posted", remaining)
    print(f"\nDone! Remaining: {remaining}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
