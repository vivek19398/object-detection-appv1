#!/bin/bash

# Stop old app
pkill -f gunicorn || true
sleep 2

# Go to repo
cd ~/OBJECT-DETECTION-APPV1 || exit 1

# Pull latest
git fetch origin
git reset --hard origin/main

# Go to backend
cd backend || exit 1

# Install deps
~/.local/bin/pip3 install -r requirements.txt --user || pip3 install -r requirements.txt --user

# Start app
~/.local/bin/gunicorn -w 2 -b 0.0.0.0:8000 -t 300 app:app > app.log 2>&1 &

# Wait and check
sleep 3
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "App started successfully"
else
    echo "Failed - check app.log"
    tail -20 app.log
    exit 1
fi
