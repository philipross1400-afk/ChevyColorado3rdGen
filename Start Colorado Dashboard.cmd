@echo off
cd /d "%~dp0"
python colorado_dashboard.py
if errorlevel 1 pause
