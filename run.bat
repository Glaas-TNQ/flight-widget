@echo off
rem Usage: run.bat FR2670   (no code: asks for one, or reuses the last)
start "" pythonw "%~dp0flight_widget.py" %*
