# -*- coding: utf-8 -*-
"""
DeviantArt OAuth2 再認証スクリプト
ローカルサーバーでOAuthコールバックを自動キャッチし、GitHub Secretsを更新する
"""
import getpass
import subprocess
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# ============================================================
print("=== DeviantArt OAuth2 再認証 ===\n")
CLIENT_ID = input("DA_CLIENT_ID を入力: ").strip()
CLIENT_SECRET = getpass.getpass("DA_CLIENT_SECRET を入力（画面には表示されません）: ").strip()

# 注: redirect_uri はDAアプリ登録の「OAuth2 Redirect URI Whitelist」と完全一致が必須。
# 登録値は http://localhost:8080（旧コードの :8432/callback は不一致で認証が無言で失敗していた）。
REDIRECT_URI = "http://localhost:8080"
SCOPE = "stash publish browse"
REPO = "MuscleLove-777/deviantart-auto-uploader"

# ============================================================
# Step 1: ローカルサーバーでコールバックを受け取る
# ============================================================
auth_code_result = {"code": None}


class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            auth_code_result["code"] = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ 認証コード取得成功！このタブは閉じてOKです。".encode("utf-8"))
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            error = query.get("error", ["unknown"])[0]
            self.wfile.write(f"❌ エラー: {error}".encode("utf-8"))
            auth_code_result["code"] = None

    def log_message(self, format, *args):
        pass  # ログ抑制


server = HTTPServer(("localhost", 8080), OAuthHandler)
server.timeout = 300

auth_url = "https://www.deviantart.com/oauth2/authorize?" + urlencode({
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPE,
})

print(f"ブラウザで認証ページを開きます...")
print(f"(ローカルサーバー {REDIRECT_URI} で最大5分コールバック待機中)\n")
webbrowser.open(auth_url)

# 1リクエストだけ受け取って終了
server.handle_request()
server.server_close()

code = auth_code_result["code"]
if not code:
    print("認証コードの取得に失敗しました。")
    sys.exit(1)

print("認証コード取得成功")

# ============================================================
# Step 2: コードをトークンに交換
# ============================================================
print("トークンを取得中...")
try:
    r = requests.post("https://www.deviantart.com/oauth2/token", data={
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }, timeout=30)
except requests.RequestException as e:
    print(f"トークン取得通信エラー: {e}")
    sys.exit(1)

if r.status_code != 200:
    print(f"エラー: {r.status_code} {r.text}")
    sys.exit(1)

data = r.json()
if 'access_token' not in data:
    print(f"エラー: {data}")
    sys.exit(1)

access_token = data['access_token']
refresh_token = data.get('refresh_token', '')

print("トークン取得成功（値は安全のため表示しません）")

# ============================================================
# Step 3: GitHub Secrets を更新
# ============================================================
print(f"\nGitHub Secrets を更新中 ({REPO})...")

for name, value in [("DA_ACCESS_TOKEN", access_token), ("DA_REFRESH_TOKEN", refresh_token)]:
    result = subprocess.run(
        ["gh", "secret", "set", name, "-R", REPO, "--body", value],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  ✅ {name} 更新完了")
    else:
        print(f"  ❌ {name} 更新失敗: {result.stderr}")

# ============================================================
# Step 4: 完了（実投稿を伴うワークフローは自動起動しない）
# ============================================================
print("\n=== 再認証完了 ===")
print("実投稿を避けるためワークフローは起動していません。次回の定期実行で確認します。")
