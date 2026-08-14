# Uniview WebSocket Viewer

Python test viewer for the Uniview `/media/flv/video2` WebSocket endpoint.

## Target

`ws://37.202.152.217:8001/media/flv/video2`

## Important discovery

The camera returns a JSON `errorCode=401` challenge *inside* the WebSocket after the initial upgrade. The browser request observed for the working camera places the Digest value in the `Cookie` header as a cookie named `Authorization`, rather than sending it as a normal HTTP `Authorization:` header.

This project therefore tests the camera's browser-compatible authentication path and records the binary media packets for protocol analysis. It does not assume that each packet is standard FLV/H.264.

## Run

```powershell
python -m pip install -r requirements.txt
python viewer.py
```

Set `CAMERA_PASSWORD` in the environment before running, or edit the configuration in `viewer.py`.

The program starts a local HTTP page in Chrome and continuously records received binary packets under `captures/`.
