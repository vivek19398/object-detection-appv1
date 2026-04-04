from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import boto3
import uuid
import json
from PIL import Image
import io
import os
import time
from datetime import datetime
import requests

app = Flask(__name__)
CORS(app)

# CONFIG
BUCKET = "object-detection-uploads-v1"
ENDPOINT = "tensorflowmodel"
REGION = "eu-west-1"
DYNAMODB_TABLE = "detection-results"
EMAIL_API_ENDPOINT = "https://l9hpmpujie.execute-api.eu-west-1.amazonaws.com/send-mail"

# AWS CLIENTS
s3 = boto3.client('s3', region_name=REGION)
runtime = boto3.client('sagemaker-runtime', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(DYNAMODB_TABLE)
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

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

# Get scene description from Bedrock
def get_scene_description(detections):
    try:
        if not detections or len(detections) == 0:
            return None
        
        # Format detected objects
        objects = ', '.join([f"{d['name']} ({d['confidence']}%)" for d in detections])
        prompt = f"An object detection model found these objects in an image: {objects}. Write one short sentence describing what the scene likely shows."
        
        # Call Bedrock Nova Micro
        response = bedrock.invoke_model(
            modelId='amazon.nova-micro-v1:0',
            body=json.dumps({
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
                "inferenceConfig": {"maxTokens": 80, "temperature": 0.5}
            })
        )
        
        result = json.loads(response['body'].read())
        return result['output']['message']['content'][0]['text'].strip()
    except Exception as e:
        print(f"[Bedrock] Error: {e}")
        return None

# Save detections to DynamoDB
def save_detections(detections, uploader_name, inference_ms, scene_description, image_id):
    try:
        timestamp = datetime.utcnow().isoformat()
        for det in detections:
            table.put_item(Item={
                'image_id': image_id,  # Changed from 'id'
                'timestamp': timestamp,
                'object_name': det['name'],
                'confidence': str(round(det['confidence'], 2)),
                'inference_ms': str(round(inference_ms, 2)),
                'uploader_name': uploader_name or 'Anonymous',
                'scene_description': scene_description or '',
                'source': 'web-upload'
            })
    except Exception as e:
        print(f"[DynamoDB] Error: {e}")
# Get analytics from DynamoDB
@app.route('/analytics', methods=['GET'])
def get_analytics():
    try:
        response = table.scan()
        items = response.get('Items', [])
        
        # Sort by timestamp descending
        items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return jsonify({
            "status": "success",
            "records": items
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })

# UPLOAD + DETECTION API
@app.route('/upload', methods=['POST'])
def upload():
    try:
        start_time = time.time()
        
        # Get file and uploader name
        file = request.files['file']
        uploader_name = request.form.get('uploader_name', 'Anonymous')
        image_id = str(uuid.uuid4())
        
        # Convert and resize image
        image = Image.open(file.stream).convert("RGB")
        
        # Resize to max 800px while maintaining aspect ratio
        max_size = (800, 800)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Convert to JPEG bytes
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        image_bytes = buffer.getvalue()
        
        # Upload to S3
        s3.put_object(
            Bucket=BUCKET,
            Key=image_id,
            Body=image_bytes,
            ContentType='image/jpeg'
        )
        
        # Call SageMaker endpoint
        response = runtime.invoke_endpoint(
            EndpointName=ENDPOINT,
            ContentType='application/x-image',
            Body=image_bytes
        )
        
        result = response['Body'].read().decode()
        parsed_result = json.loads(result)
        
        inference_ms = (time.time() - start_time) * 1000
        
        # Format detections
        classes = parsed_result.get('classes', [])
        scores = parsed_result.get('scores', [])
        
        detections = []
        for i, (cls, score) in enumerate(zip(classes, scores)):
            if score > 0.3:  # Filter low confidence
                detections.append({
                    'name': get_coco_label(int(cls)),
                    'confidence': round(float(score) * 100, 1)
                })
        
        # Get top 10 detections
        detections.sort(key=lambda x: x['confidence'], reverse=True)
        detections = detections[:10]
        
        # Get scene description from Bedrock
        scene_description = get_scene_description(detections)
        
        # Save to DynamoDB
        save_detections(detections, uploader_name, inference_ms, scene_description, image_id)
        
        return jsonify({
            "status": "success",
            "image_id": image_id,
            "s3_url": f"https://{BUCKET}.s3.amazonaws.com/{image_id}",
            "detections": detections,
            "inference_ms": round(inference_ms, 1),
            "scene_description": scene_description
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })

@app.route('/send-email', methods=['POST'])
def send_email():
    """
    Endpoint to trigger Lambda function for sending email
    """
    try:
        data = request.get_json()
        
        to_email = data.get('to_email')
        uploader_name = data.get('uploader_name')
        detections = data.get('detections', [])
        scene_description = data.get('scene_description', '')
        inference_ms = data.get('inference_ms', 0)
        image_url = data.get('image_url', '')
        
        if not to_email:
            return jsonify({
                "status": "error",
                "message": "Email address is required"
            }), 400
        
        # Call Lambda via API Gateway
        response = requests.post(
            EMAIL_API_ENDPOINT,
            json={
                'to_email': to_email,
                'uploader_name': uploader_name,
                'detections': detections,
                'scene_description': scene_description,
                'inference_ms': inference_ms,
                'image_url': image_url
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return jsonify({
                "status": "success",
                "message": f"Email sent to {to_email}"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to send email"
            }), 500
            
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# COCO class names
COCO_CLASSES = ['person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush']

def get_coco_label(idx):
    if 0 <= idx < len(COCO_CLASSES):
        return COCO_CLASSES[idx]
    return 'unknown'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
