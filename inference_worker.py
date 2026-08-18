import queue
import threading
import os
import datetime
import io
import random
import time
from PIL import Image
# Gracefully handle missing PyTorch/Torchvision dependencies
try:
    import torch
    import torch.nn as nn
    from torchvision import models, transforms
    HAS_TORCH = True
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
except ImportError:
    HAS_TORCH = False
    DEVICE = "cpu"
    # Stub classes so Python parsing doesn't raise NameError
    class nn:
        class Module:
            def __init__(self): pass
            def register_buffer(self, *args, **kwargs): pass
            def __call__(self, x): return x
        class Sequential:
            def __init__(self, *args): pass
        class Conv2d:
            def __init__(self, *args, **kwargs): pass
        class BatchNorm2d:
            def __init__(self, *args, **kwargs): pass
        class ReLU:
            def __init__(self, *args, **kwargs): pass
        class Linear:
            def __init__(self, *args, **kwargs): pass
            
    models = None
    transforms = None

from database import get_db_connection
from logger import log_event

# -------------------------------------------------------------------
# Model Configurations & Device Setup
# -------------------------------------------------------------------
MODEL_PATH = "best_wavelet_resnet_model.pth"
SAVE_DIR = "captures"
os.makedirs(SAVE_DIR, exist_ok=True)

class HaarDWT2D(nn.Module):
    """2D Haar Wavelet Decomposition."""
    def __init__(self):
        super().__init__()
        if HAS_TORCH:
            self.register_buffer("pL", torch.tensor([0.5, 0.5], dtype=torch.float32))
            self.register_buffer("pH", torch.tensor([0.5, -0.5], dtype=torch.float32))

    def forward(self, x):
        if not HAS_TORCH:
            return x
        B, C, H, W = x.shape
        if H % 2 != 0 or W % 2 != 0:
            raise ValueError("Input height and width must be even for HaarDWT2D.")

        x_grouped = x.view(B * C, 1, H, W)
        k_LL = torch.outer(self.pL, self.pL).view(1, 1, 2, 2)
        k_LH = torch.outer(self.pL, self.pH).view(1, 1, 2, 2)
        k_HL = torch.outer(self.pH, self.pL).view(1, 1, 2, 2)
        k_HH = torch.outer(self.pH, self.pH).view(1, 1, 2, 2)

        kernels = torch.cat([k_LL, k_LH, k_HL, k_HH], dim=0).to(x.device)
        sub_bands = nn.functional.conv2d(x_grouped, kernels, stride=2)
        sub_bands = sub_bands.view(B, C, 4, H // 2, W // 2)
        return sub_bands.reshape(B, C * 4, H // 2, W // 2)

class WaveletEnhancedResNet(nn.Module):
    def __init__(self, num_classes, backbone_name="resnet50"):
        super().__init__()
        if HAS_TORCH:
            self.dwt = HaarDWT2D()
            self.frequency_projector = nn.Sequential(
                nn.Conv2d(in_channels=12, out_channels=3, kernel_size=3, padding=1, bias=False),
                nn.BatchNorm2d(3),
                nn.ReLU(inplace=True),
            )

            if backbone_name == "resnet18":
                self.backbone = models.resnet18(weights=None)
            elif backbone_name == "resnet34":
                self.backbone = models.resnet34(weights=None)
            elif backbone_name == "resnet50":
                self.backbone = models.resnet50(weights=None)
            elif backbone_name == "resnet101":
                self.backbone = models.resnet101(weights=None)
            else:
                raise ValueError("Choose one of: resnet18, resnet34, resnet50, resnet101")

            in_features = self.backbone.fc.in_features
            self.backbone.fc = nn.Linear(in_features, num_classes)

    def forward(self, x):
        if not HAS_TORCH:
            return x
        x_freq = self.dwt(x)
        x_encoded = self.frequency_projector(x_freq)
        return self.backbone(x_encoded)

def load_model():
    """Loads the wavelet-enhanced ResNet model, falling back to None if model file is missing or PyTorch is absent."""
    if not HAS_TORCH:
        log_event("WARNING", "Inference", "PyTorch is not installed in the environment. Fallback to mock inference engine.")
        return None
        
    if not os.path.exists(MODEL_PATH):
        log_event("WARNING", "Inference", f"Model path '{MODEL_PATH}' not found. Fallback to mock inference engine.")
        return None
    try:
        checkpoint = torch.load(MODEL_PATH, map_location=DEVICE)
        if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
            checkpoint = checkpoint["state_dict"]

        if isinstance(checkpoint, dict) and any(
            k.startswith("dwt") or k.startswith("frequency_projector") or k.startswith("backbone")
            for k in checkpoint.keys()
        ):
            num_classes = checkpoint["backbone.fc.weight"].shape[0]
            model = WaveletEnhancedResNet(num_classes=num_classes, backbone_name="resnet50").to(DEVICE)
            model.load_state_dict(checkpoint, strict=True)
            model.eval()
            log_event("INFO", "Inference", f"Successfully loaded WaveletEnhancedResNet with {num_classes} classes.")
            return model

        if isinstance(checkpoint, nn.Module):
            model = checkpoint.to(DEVICE)
            model.eval()
            log_event("INFO", "Inference", "Successfully loaded PyTorch Module.")
            return model

        raise TypeError("The checkpoint format is not recognized")
    except Exception as e:
        log_event("ERROR", "Inference", f"Failed to load model file. Fallback to mock inference engine: {e}")
        return None

# Load global model instance
model = load_model()

# Image transforms matching training setup
if HAS_TORCH:
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
else:
    transform = None


def run_inference(image_bytes):
    """Processes raw JPEG bytes through model or mock runner."""
    if model is None:
        # Mock prediction helper (returns a class 0-4)
        time.sleep(0.08)  # simulate brief network/model lag
        # Return 0 (no pests) 70% of time, 1-4 (pests) 30% of time
        return random.choices([0, 1, 2, 3, 4], weights=[70, 10, 10, 5, 5])[0]

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        tensor_img = transform(img).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            outputs = model(tensor_img)
            _, predicted_class = torch.max(outputs, 1)

        return predicted_class.item()
    except Exception as e:
        log_event("ERROR", "Inference", f"Error during model run_inference: {e}")
        return None

# -------------------------------------------------------------------
# Queue & Worker Thread Implementation
# -------------------------------------------------------------------
MAX_QUEUE_SIZE = 50
inference_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)

def worker_loop():
    """Background queue listener processing inference jobs."""
    log_event("INFO", "Inference", "Background inference worker thread started.")
    while True:
        job = inference_queue.get()
        if job is None:
            # Shutdown signal
            break
        
        log_id = job["log_id"]
        image_bytes = job["image_bytes"]
        node_id = job["node_id"]

        # Update status to processing
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE capture_logs 
                SET processing_status = 'processing' 
                WHERE id = ?
                """, (log_id,))
                conn.commit()
            
            # Execute model
            prediction = run_inference(image_bytes)
            
            status = "completed" if prediction is not None else "failed"
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE capture_logs 
                SET processing_status = ?, prediction = ? 
                WHERE id = ?
                """, (status, prediction, log_id))
                conn.commit()
            
            log_event("INFO", "Inference", f"Processed capture ID {log_id} from {node_id}. Result: {prediction}", node_id=node_id)
        except Exception as e:
            log_event("ERROR", "Inference", f"Exception processing capture ID {log_id} from {node_id}: {e}", node_id=node_id)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                    UPDATE capture_logs 
                    SET processing_status = 'failed' 
                    WHERE id = ?
                    """, (log_id,))
                    conn.commit()
            except Exception:
                pass
        finally:
            inference_queue.task_done()

# Start the background worker thread
worker_thread = threading.Thread(target=worker_loop, name="InferenceWorker", daemon=True)
worker_thread.start()

def enqueue_capture_job(node_id, image_bytes, trigger_source, sensor_log_id=None):
    """
    Saves the image to disk, inserts a pending record into SQLite database,
    and enqueues the record ID for background inference.
    
    Returns log_id on success, or None if the queue is full.
    """
    received_at = datetime.datetime.now()
    filename = os.path.join(SAVE_DIR, f"capture_{node_id}_{received_at.strftime('%Y%m%d_%H%M%S_%f')}.jpg")
    
    # 1. Save file to captures/ directory
    try:
        with open(filename, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        log_event("ERROR", "Inference", f"Failed to save image file on disk: {e}", node_id=node_id)
        return None

    # 2. Insert record in capture_logs
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO capture_logs (node_id, timestamp, file_path, trigger_source, sensor_log_id, processing_status)
            VALUES (?, datetime('now', 'localtime'), ?, ?, ?, 'pending')
            """, (node_id, filename, trigger_source, sensor_log_id))
            log_id = cursor.lastrowid
            conn.commit()
    except Exception as e:
        log_event("ERROR", "Inference", f"Failed to insert database capture log: {e}", node_id=node_id)
        return None

    # 3. Push job into the processing queue
    try:
        inference_queue.put_nowait({
            "log_id": log_id,
            "node_id": node_id,
            "image_bytes": image_bytes
        })
        return log_id
    except queue.Full:
        log_event("CRITICAL", "Inference", f"Inference queue overflow (size={MAX_QUEUE_SIZE}). Discarded capture ID {log_id}!", node_id=node_id)
        # Update database entry to failed
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE capture_logs 
                SET processing_status = 'failed' 
                WHERE id = ?
                """, (log_id,))
                conn.commit()
        except Exception:
            pass
        return None
