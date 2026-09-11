#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "Installing dependencies..."
python3 -m pip install -r requirements.txt -q
echo
echo "Starting application..."
python3 app.py
