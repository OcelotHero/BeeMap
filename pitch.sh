#!/usr/bin/env bash
# BeeMap pitch demo: fresh data, server, simulated hive cameras, live hornets.
# Ctrl+C stops everything. Use another port with:  PORT=8010 ./pitch.sh
set -u
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
PORT=${PORT:-8000}

# 1. Python 3.10 or newer
if [ "$($PY -c 'import sys; print(int(sys.version_info >= (3, 10)))' 2>/dev/null)" != "1" ]; then
  echo "BeeMap needs Python 3.10 or newer. Found: $($PY --version 2>&1 || echo 'no python3')"
  echo "Install a newer Python, or point to one with:  PYTHON=python3.11 ./pitch.sh"
  exit 1
fi

# 2. Requirements
echo "Checking requirements…"
$PY -m pip install -q -r server/requirements.txt 2>/dev/null \
  || $PY -m pip install -q --break-system-packages -r server/requirements.txt \
  || { echo "Could not install the requirements (error above). Try a virtual environment:"; \
       echo "  $PY -m venv .venv && source .venv/bin/activate && PYTHON=python ./pitch.sh"; exit 1; }

# (OpenStreetMap land cover and iNaturalist bloom times are fetched by the
#  server itself, in the background, the first time they're needed.)

# 3. Port free?
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  echo "Port $PORT is already in use (maybe an earlier BeeMap is still running)."
  echo "Stop it, or use another port:  PORT=8010 ./pitch.sh"
  exit 1
fi

# 4. Start the server and wait until it really answers
rm -rf server/data /tmp/beemap-demo-queue
mkdir -p logs
(cd server && exec $PY -m uvicorn app:app --host 0.0.0.0 --port "$PORT") > logs/server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
printf "Starting the server"
for i in $(seq 1 60); do
  if ! kill -0 "$SERVER" 2>/dev/null; then
    echo; echo "The server stopped while starting. Its last messages:"; echo "----"
    tail -n 25 logs/server.log; echo "----"; exit 1
  fi
  if $PY -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/health', timeout=1)" 2>/dev/null; then
    echo " ready."; break
  fi
  printf "."; sleep 0.5
  if [ "$i" = 60 ]; then echo; echo "The server didn't answer within 30 s. Log:"; tail -n 25 logs/server.log; exit 1; fi
done

echo "Open http://localhost:$PORT   (server log: logs/server.log)"
$PY tools/demo.py --server "http://127.0.0.1:$PORT" --live
