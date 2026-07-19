@echo off
REM DeviantArt投稿キューの自動補充 (refill_deviantart_queue.py) を隠し実行するラッパー。
REM Task Scheduler から wscript(run_hidden.vbs) 経由で日次起動される。
cd /d "%~dp0"
set "PY=%USERPROFILE%\AppData\Local\Python\pythoncore-3.14-64\python.exe"
"%PY%" "%~dp0refill_deviantart_queue.py" >> "%~dp0refill_run.log" 2>&1
