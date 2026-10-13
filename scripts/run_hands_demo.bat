@echo off
REM H-01 - double-click: the hands-only demo. Webcam 0, mirrored (a selfie view), with the demo window.
REM   Raise your RIGHT hand and check the letter in the window; pass other arguments to change the source, e.g.
REM   run_hands_demo.bat --video "http://192.168.1.5:3000/viewer?room=room-abc123"   (set CAMERA_PIN first)
REM Needs the hand models once:  .venv\Scripts\python.exe tools\model\fetch_hand_models.py
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run setup_sidecar.ps1 first.
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\python.exe tools\hands\hands_udp_sender.py --video camera:0 --flip --show
) else (
  .venv\Scripts\python.exe tools\hands\hands_udp_sender.py --show %*
)
