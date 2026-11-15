@echo off
title RDR Modding Studio
cd /d "%~dp003_Tools"
echo Starting RDR Modding Studio at http://127.0.0.1:8731  (close this window to stop)
py -3 RDR_Studio.py
if errorlevel 1 pause
