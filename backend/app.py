from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import boto3
import uuid
import json
import base64
from PIL import Image
import io
import os

app = Flask(__name__)
CORS(app)

# CONFIG
BUCKET = "object-detection-uploads-v1"
ENDPOINT = "jumpstart-dft-mobilenet-v2-fpnlite-20260403-130823"
REGION = "eu-west-1"

# AWS CLIENTS
s3 = boto3.client('s3', region_name=REGION)
runtime = boto3.client('sagemaker-runtime', region_name=REGION)

# Serve frontend
@app.route('/')
def index():
    frontend_path = os.path.join(os.path.dirname(__file__), '../frontend')
    return send_from_directory(frontend_path, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    frontend_path = os.path.join(os.path.dirname(__file__), '../frontend')
    try:
        return send_from_directory(frontend_path, path)
    except:
        return send_from_directory(frontend_path, 'index.html')

# Health check
@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

# UPLOAD + DETECTION API
@app.route('/upload', methods=['POST'])
def upload():
    try:
        # Get file
        file = request.files['file']
        image_id = str(uuid.uuid4())
        
        # Convert image properly
        image = Image.open(file.stream).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()
        
        # Upload to S3
        s3.put_object(
            Bucket=BUCKET,
            Key=image_id,
            Body=image_bytes,
            ContentType='image/jpeg'
        )
        
        # Encode image to base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Format payload as JSON
        payload = json.dumps({
            "instances": [{"data": image_base64}]
        })
        
        # Call SageMaker endpoint
        response = runtime.invoke_endpoint(
            EndpointName=ENDPOINT,
            ContentType='application/json',
            Body=payload
        )
        
        result = response['Body'].read().decode()
        
        # Parse JSON safely
        try:
            parsed_result = json.loads(result)
        except:
            parsed_result = result
        
        return jsonify({
            "status": "success",
            "image_id": image_id,
            "s3_url": f"https://{BUCKET}.s3.amazonaws.com/{image_id}",
            "detections": parsed_result
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
