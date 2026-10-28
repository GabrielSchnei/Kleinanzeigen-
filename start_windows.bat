@echo off
REM Startet den PS5-Scanner unter Windows. Beim ersten Mal wird alles eingerichtet.
cd /d "%~dp0"
if not exist .venv (
    echo Richte Python-Umgebung ein ...
    python -m venv .venv || (echo Python nicht gefunden - bitte von python.org installieren & pause & exit /b 1)
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt playwright
    .venv\Scripts\python -m playwright install chromium
)
if not exist config.yaml copy config.example.yaml config.yaml
if not exist .env (
    copy .env.example .env
    echo Bitte .env mit Telegram-Token, Chat-ID und Claude-API-Key ausfuellen und dann neu starten.
    notepad .env
    pause
    exit /b 0
)
.venv\Scripts\python -m ps5_scanner %*
pause
