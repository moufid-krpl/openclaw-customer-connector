#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
export CONNECTOR_CONFIG_PATH="$(pwd)/config/config.json"
source .venv/bin/activate
python app/main.py
