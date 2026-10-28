#!/usr/bin/env bash
# Startet den PS5-Scanner unter Linux/macOS. Beim ersten Mal wird alles eingerichtet.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
[ -f config.yaml ] || cp config.example.yaml config.yaml
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Bitte .env mit Telegram-Token, Chat-ID und Claude-API-Key ausfüllen und dann neu starten."
  exit 0
fi
exec .venv/bin/python -m ps5_scanner "$@"
