import asyncio
import os
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

CAMERAS = {
    "1": os.getenv("CAMERA1_RTSP", "rtsp://admin:CHANGE_ME@37.202.152.217:8554/media/video1"),
    "2": os.getenv("CAMERA2_RTSP", "rtsp://admin:CHANGE_ME@37.202.152.217:8552/media/video1"),
}

pcs = set()

async def index(request):
    return web.FileResponse("web/index.html")

async def offer(request):
    body = await request.json()
    camera = body.get("camera", "1")
    url = CAMERAS.get(camera)
    if not url:
        return web.json_response({"error": "unknown camera"}, status=400)

    pc = RTCPeerConnection()
    pcs.add(pc)

    player = MediaPlayer(url, format="rtsp", options={
        "rtsp_transport": "tcp",
        "stimeout": "5000000",
        "fflags": "nobuffer",
        "flags": "low_delay",
    })

    if player.video:
        pc.addTrack(player.video)

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(body["sdp"], body["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def cleanup(app):
    await asyncio.gather(*(pc.close() for pc in pcs), return_exceptions=True)
    pcs.clear()

app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/offer", offer)
app.on_shutdown.append(cleanup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
