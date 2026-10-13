@echo off
REM H-02 - double-click: the face-only demo. Webcam 0, mirrored (a selfie view), with the demo window.
REM   Sit within ~1.5 m with your face filling a good part of the picture (the detector is short range).
REM   Pass other arguments to change the source, e.g.
REM   run_face_demo.bat --video "http://192.168.1.5:3000/viewer?room=room-abc123"   (set CAMERA_PIN first)
REM Needs the face models once:  .venv\Scripts\python.exe tools\model\fetch_face_models.py
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run setup_sidecar.ps1 first.
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\python.exe tools\face\face_udp_sender.py --video camera:0 --flip --show
) else (
  .venv\Scripts\python.exe tools\face\face_udp_sender.py --show %*
)
