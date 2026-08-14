# MobileCameraAI — Browser Live Proxy

GitHub Pages cannot directly open the camera's `ws://` stream because the page is HTTPS and the camera requires Digest-authenticated WebSocket access. The verified camera stream is `ws://37.202.152.217:8001/media/flv/video2`.

This directory documents the free proxy architecture: GitHub Pages -> HTTPS WebSocket proxy -> Uniview WS. The proxy must be deployed on a service that supports WebSocket upgrades (for example a free-tier edge worker). **Do not put the camera password in this repository.**

Required proxy behavior:
1. Accept a WebSocket upgrade from the GitHub Pages viewer.
2. Authenticate to Uniview using HTTP Digest credentials held as deployment secrets.
3. Open the upstream WebSocket to the selected camera stream.
4. Pipe binary WebSocket frames in both directions.
5. Expose `wss://<proxy-host>/camera/1` and `/camera/2` to the viewer.
