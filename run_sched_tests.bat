@echo off
cd /d E:\python_scripts\2026\kaz_nedviga
python -m pytest tests/test_scheduler.py --tb=line -q --timeout=30
echo EXIT CODE: %ERRORLEVEL%
