@echo off
title Greyhound Precision Engine Server
cd /d "C:\Users\apple\.gemini\antigravity\scratch\greyhound-betting-engine"
echo ========================================================
echo Starting Greyhound Precision Multi-Betting Engine...
echo Dashboard will be available at: http://127.0.0.1:8090
echo ========================================================
python -m uvicorn app:app --host 127.0.0.1 --port 8090 --reload
pause
