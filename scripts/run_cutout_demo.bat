@echo off
REM S-01 - double-click: the cutout demo. Webcam 0, with the demo window (the picture, the person cut out of it, the mask).
REM   Stand 1-3 m from the camera with your whole body in the picture. The sender sends the picture AND the mask over UDP to
REM   127.0.0.1:8903 as well, so the Unity Cutout showcase (or tools\cutout\cutout_udp_receiver.py --view) can show it.
REM   Unity mirrors the picture itself (instantly, on the GPU), so there is no --flip here.
REM   Pass other arguments to change the source, e.g.
REM   run_cutout_demo.bat --video "http://192.168.1.5:3000/viewer?room=room-abc123"   (set CAMERA_PIN first)
REM   run_cutout_demo.bat --list-cameras
REM Needs the two selfie networks once:  .venv\Scripts\python.exe tools\model\fetch_cutout_models.py
REM and, for one crop per person, the person detector:  .venv\Scripts\python.exe tools\model\fetch_yolox_s.py
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run setup_sidecar.ps1 first.
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\python.exe tools\cutout\cutout_udp_sender.py --video camera:0 --show
) else (
  .venv\Scripts\python.exe tools\cutout\cutout_udp_sender.py --show %*
)
