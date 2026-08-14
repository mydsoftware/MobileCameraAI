#!/data/data/com.termux/files/usr/bin/bash
set -e

REPO="https://github.com/mydsoftware/MobileCameraAI.git"
DIR="$HOME/MobileCameraAI"
APP="$DIR/uniview-ws-viewer"

clear
echo "========================================"
echo " Uniview MobileCameraAI - One Click"
echo "========================================"

pkg update -y
pkg install -y git python ffmpeg

if [ -d "$DIR/.git" ]; then
  cd "$DIR"
  git pull --ff-only
else
  git clone "$REPO" "$DIR"
fi

cd "$APP"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

chmod +x run.sh 2>/dev/null || true

if [ -f .env ]; then
  echo "Using existing camera configuration."
else
  echo ""
  echo "Camera 1: 37.202.152.217:8001"
  echo "Camera 2: 37.202.152.217:8002"
  echo ""
  read -r -p "Camera username [admin]: " USERNAME
  USERNAME=${USERNAME:-admin}
  read -r -s -p "Camera password: " PASSWORD
  echo
  cat > .env <<EOF
CAMERA_USERNAME=$USERNAME
CAMERA_PASSWORD=$PASSWORD
LOCAL_PORT=5050
EOF
fi

export $(grep -v '^#' .env | xargs)
python viewer.py
