# -*- coding: utf-8 -*-
r"""
DeviantArt投稿キューの自動補充（枯れない化 / 憲法「完全自動運営」対応）。

背景（2026-07-20 の障害）:
- 投稿機は gdown で GDRIVE_FOLDER_ID フォルダを丸ごと取得していたが、
  mldrive:DeviantArt は18,000枚超あり Google 側のレート制限で毎回同じ先頭85枚しか
  ダウンロードできていなかった。その85枚は全部投稿済みのため
  「All files already uploaded」= no_content と誤判定し、ジョブが赤く落ち続けた。
- 対策として ameblo と同じキュー方式に統一する。

仕組み:
- 投稿機は GDRIVE_FOLDER_ID = キュー(_deviantart_queue, gdownが確実に全DLできる小さなフォルダ)
  から未投稿1枚を投稿する。
- 本スクリプトが定期実行で:
    1) キュー内の「投稿済み」画像を削除(プルーン)
    2) deep pool (mldrive:DeviantArt 全体, キュー類は除外) から
       未投稿・一意basename・200MB以下のファイルを選び、キューを TARGET 枚まで補充
- サブフォルダ構成は rclone copy --files-from が保持するため、
  upload.py の generate_tags()（パス名からタグ推定）はそのまま機能する。

注意: キューフォルダは「ファイルの追加/削除」のみ。フォルダ自体は作り直さない
      (作り直すと folder ID が変わり GDRIVE_FOLDER_ID secret が無効になるため)。
"""
import collections
import glob
import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _find_rclone():
    # タスク実行時は PATH 上の "rclone" が別物に解決されることがあるため絶対パスでピン留め。
    pats = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Rclone.Rclone_*\rclone-*\rclone.exe"),
    ]
    for pat in pats:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    shim = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\rclone.exe")
    return shim if os.path.isfile(shim) else "rclone"


RCLONE_BIN = _find_rclone()

DA_DIR = Path(r"C:\Users\atsus\000_ClaudeCode\40_MuscleLove\001_集客\deviantart-auto-uploader")
REMOTE_ROOT = "mldrive:DeviantArt"
QUEUE_NAME = "_deviantart_queue"
QUEUE = f"{REMOTE_ROOT}/{QUEUE_NAME}"
QUEUE_ID = "1VbYUxS12NOxGtblO3nDk7Ds5nU6937gL"  # = GDRIVE_FOLDER_ID
TARGET = 40
MAX_SIZE = 200 * 1024 * 1024  # DeviantArt上限
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".webm")
# 他プラットフォームのキューは deep pool から除外する
EXCLUDE_PREFIXES = (QUEUE_NAME + "/",)
EXCLUDE_SUBSTR = ("_ameblo_queue/",)
LOG = DA_DIR / "queue_refill.log"
# タスクスケジューラ文脈では %APPDATA%\rclone\rclone.conf が不可視になるため、
# リポジトリ内のローカルコピー(.gitignore済み)を最優先で使う。
_LOCAL_CONF = DA_DIR / ".rclone_mldrive.conf"
if _LOCAL_CONF.is_file():
    RCLONE_CONF = str(_LOCAL_CONF)
else:
    RCLONE_CONF = os.path.expandvars(r"%APPDATA%\rclone\rclone.conf")
    if not os.path.isfile(RCLONE_CONF):
        RCLONE_CONF = r"C:\Users\atsus\AppData\Roaming\rclone\rclone.conf"


def log(msg):
    print(msg, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def rclone(*args):
    return subprocess.run([RCLONE_BIN, "--config", RCLONE_CONF, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def lsf(path):
    """相対パスの一覧（拡張子フィルタのみ）。キュー等の小さいフォルダ用。"""
    r = rclone("lsf", path, "-R", "--files-only")
    if r.returncode != 0:
        log(f"  rclone lsf失敗 {path}: {(r.stderr or '')[:160]}")
        return []
    return [l.strip() for l in (r.stdout or "").splitlines()
            if l.strip().lower().endswith(EXTS)]


def pool_files():
    """deep pool の (相対パス) 一覧。サイズ上限と除外プレフィックスを適用。"""
    r = rclone("lsjson", REMOTE_ROOT, "-R", "--files-only", "--no-modtime", "--no-mimetype")
    if r.returncode != 0:
        log(f"  rclone lsjson失敗: {(r.stderr or '')[:200]}")
        return []
    try:
        items = json.loads(r.stdout or "[]")
    except Exception as e:
        log(f"  lsjson解析失敗: {e}")
        return []
    out = []
    for it in items:
        p = it.get("Path", "")
        if not p.lower().endswith(EXTS):
            continue
        if p.startswith(EXCLUDE_PREFIXES) or any(s in p for s in EXCLUDE_SUBSTR):
            continue
        if int(it.get("Size", 0) or 0) > MAX_SIZE:
            continue
        out.append(p)
    return out


def base(p):
    return p.split("/")[-1]


def posted_set():
    """origin/master の uploaded.json（投稿機が毎回pushする正本）から投稿済みbasenameを取る。"""
    subprocess.run(["git", "-C", str(DA_DIR), "fetch", "-q", "origin", "master"],
                   capture_output=True, text=True)
    r = subprocess.run(["git", "-C", str(DA_DIR), "show", "origin/master:uploaded.json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout)
        return {e["file"] if isinstance(e, dict) else e for e in data.get("files", [])}
    except Exception as e:
        log(f"  posted読込失敗: {e}")
        return set()


def main():
    log(f"=== refill {datetime.now().isoformat(timespec='seconds')} ===")
    posted = posted_set()
    if not posted:
        # 空セットで進むとプルーンが効かず補充判定も狂うので中断する
        log("⚠ uploaded.json を読めなかったため中断（誤補充防止）")
        return 1

    qfiles = lsf(QUEUE)
    log(f"queue現在={len(qfiles)}  posted記録={len(posted)}")

    # 1) 投稿済みをキューから削除
    pruned = 0
    for qf in qfiles:
        if base(qf) in posted:
            r = rclone("deletefile", f"{QUEUE}/{qf}")
            if r.returncode == 0:
                pruned += 1
            else:
                log(f"  delete失敗 {qf}: {(r.stderr or '')[:100]}")
    qfiles = [q for q in qfiles if base(q) not in posted]
    log(f"投稿済み{pruned}枚プルーン → queue={len(qfiles)}")

    need = TARGET - len(qfiles)
    if need <= 0:
        log(f"補充不要 (queue>={TARGET})")
        log("=== 完了 ===")
        return 0

    # 2) deep pool から未投稿・一意basenameを補充
    #    （投稿機の重複判定が basename 単位のため、同名ファイルは候補から外す）
    allp = pool_files()
    bn_count = collections.Counter(base(p) for p in allp)
    qbn = {base(q) for q in qfiles}
    cand = [p for p in allp
            if base(p) not in posted and base(p) not in qbn and bn_count[base(p)] == 1]
    log(f"pool={len(allp)}  補充候補={len(cand)}")
    if not cand:
        log("⚠ 補充候補なし(プール枯渇)。mldrive:DeviantArt へ新規画像追加が必要。")
        return 0
    random.shuffle(cand)
    picks = cand[:need]

    ff = Path(tempfile.mktemp(suffix=".txt"))
    ff.write_text("\n".join(picks), encoding="utf-8")
    r = rclone("copy", REMOTE_ROOT, QUEUE, "--files-from", str(ff))
    try:
        ff.unlink()
    except Exception:
        pass
    if r.returncode != 0:
        log(f"  補充copy失敗: {(r.stderr or '')[:160]}")
        return 1
    log(f"補充{len(picks)}枚 → queue={len(qfiles) + len(picks)}  (プール残り候補~{len(cand) - len(picks)})")
    log("=== 完了 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
