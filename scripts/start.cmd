@echo off
rem Startet VGrow Cockpit (inoffiziell). Doppelklick reicht. Fenster offen lassen, solange es laufen soll.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m vgrow %*
