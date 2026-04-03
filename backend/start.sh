#!/bin/bash

echo "Stopping old app..."
pkill gunicorn || true

echo "Pulling latest code..."
git pull origin main

echo "Installing dependencies..."
pip3 install -r requirements.txt

echo "Starting app..."
nohup python3 -m gunicorn -w 2 -b 0.0.0.0:8000 app:app > app.log 2>&1 &