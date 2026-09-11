#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m pytest tests/test_scheduler.py --tb=line -q
echo "EXIT: $?"
