#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
if [ ! -f config/config.json ]; then
  cp config/config.example.json config/config.json
  echo "config/config.json created from example. Please edit it before first productive start."
fi
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "Setup completed. Start with scripts/run_connector.sh"
