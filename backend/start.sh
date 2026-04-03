#!/bin/bash
set -e

echo "Stopping old app..."
pkill -f "gunicorn" || true
sleep 2

echo "Pulling latest code..."
cd ~/OBJECT-DETECTION-APPV1
git pull origin main

echo "Installing dependencies..."
cd backend
pip3 install -r requirements.txt --break-system-packages

echo "Starting app..."
cd ~/OBJECT-DETECTION-APPV1/backend
nohup gunicorn -w 2 -b 0.0.0.0:8000 -t 300 app:app > app.log 2>&1 &

sleep 3

echo "Checking if app started..."
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ App started successfully"
else
    echo "⚠️  App might not have started properly"
    tail -20 app.log
fi