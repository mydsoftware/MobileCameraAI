import os
import shutil
import subprocess
import threading
import time

from viewer import LOCAL_PORT, app, start_stream, DEFAULT_PORT, DEFAULT_PATH

VLC_URL = f"http://127.0.0.1:{LOCAL_PORT}/live.mjpg"


def launch_vlc():
    time.sleep(2.5)

    # Android / Termux: open the local MJPEG stream directly in VLC.
    if os.path.exists("/system/bin/am") or os.getenv("ANDROID_ROOT"):
        commands = [
            ["am", "start", "-n", "org.videolan.vlc/.StartActivity", "-d", VLC_URL],
            ["termux-open-url", VLC_URL],
        ]
        for cmd in commands:
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("VLC opened:", VLC_URL)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
        print("VLC could not be launched automatically.")
        print("Open this URL in VLC:", VLC_URL)
        return

    # Windows / desktop VLC.
    candidates = [
        shutil.which("vlc"),
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, VLC_URL])
            print("VLC opened:", VLC_URL)
            return

    print("VLC executable was not found.")
    print("Open this URL in VLC:", VLC_URL)


def main():
    print("Uniview -> WebSocket -> FLV -> FFmpeg -> MJPEG -> VLC")
    print("Stream URL:", VLC_URL)
    start_stream(DEFAULT_PORT, DEFAULT_PATH)
    threading.Thread(target=launch_vlc, daemon=True).start()
    app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
