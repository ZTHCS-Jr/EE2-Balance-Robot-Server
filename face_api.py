"""Server-side face recognition: 

Custom API - built by Samuel :) - to load our face detection/recognition models. 
Ensures everything happens concurrently and synchronous with locks (thanks sarim).
Note: on first run it will take awhile to install the buffalo model
"""


from __future__ import annotations
import threading
from typing import List, Optional, Tuple
import cv2
import numpy as np
import config
from insightface.app import FaceAnalysis
from face_db import FaceDB

class FaceAPI():
    def __init__(self):
        # A single shared lock around all InsightFace calls. The underlying ONNX
        # session is not guaranteed to be thread-safe for concurrent .get() calls,
        # and at our scale serialising detection is fine.
        self._lock = threading.Lock()
        self._app = None
        self._db: Optional[FaceDB] = None
        self._init_error: Optional[str] = None

    def build_face_app(self):
        # Loads MODEL_name (insight face buffalo_l) for the detection and recognition (RetinaFace and ArcFace)
        app = FaceAnalysis(name=config.MODEL_NAME, providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=config.DET_SIZE)
        return app


    def largest_face(self, faces):
        # Return the biggest detected face (by bbox area), or None if no faces
        if not faces:
            return None
        return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    def _ensure_ready(self) -> bool:
        # Initialise the model and database
        if self._app is not None and self._db is not None:
            return True
        try:
            self._app = self.build_face_app()
            self._db = FaceDB.load()
        except Exception as exc:  # Raise exception if insightface load fails
            self._init_error = str(exc)
            print(f"[face_api] InsightFace unavailable: {exc}")
            return False
        self._init_error = None
        return True

    def init_error(self) -> Optional[str]:
        # Last initialisation failure message, or None if the engine is ready.
        return self._init_error



    # 
    def detect(self, jpeg_bytes: bytes) -> List[dict]:
        # Decodes JPEG and passes to model
        if not self._ensure_ready():
            return []
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []
        with self._lock:
            faces = self._app.get(img)
            results = []
            # Each result is {"bbox": [x1,y1,x2,y2], "name": str|None, "score": float}
            for face in faces:
                name, score = self._db.identify(face.normed_embedding)
                b = face.bbox.astype(int).tolist()
                results.append({"bbox": b, "name": name, "score": float(score)})
        return results


    # Enroll each uploaded image, turn into embedding and upload to db
    def enroll_image(self, name: str, jpeg_bytes: bytes) -> Tuple[bool, str, int]:
        # Run detection on one uploaded image and add the largest face to `name`.
        if not self._ensure_ready():
            return False, "Face engine not available", 0
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return False, "Could not decode image", self._db.count_for(name) if self._db else 0
        with self._lock:
            face = self.largest_face(self._app.get(img))
            if face is None:
                return False, "No face detected", self._db.count_for(name)
            self._db.add(name, face.normed_embedding)
            self._db.save()
            return True, "ok", self._db.count_for(name)


    # Display at the bottom of the people registered
    def list_people(self) -> List[dict]:
        # Return [{name, count}] for every enrolled person.
        if not self._ensure_ready():
            return []
        with self._lock:
            return [{"name": n, "count": self._db.count_for(n)} for n in self._db.people()]


    def delete_person(self, name: str) -> Tuple[bool, List[str]]:
        """Remove every embedding under `name`. Returns (existed, remaining_names)."""
        if not self._ensure_ready():
            return False, []
        with self._lock:
            existed = name in self._db.labels
            self._db.remove(name)
            self._db.save()
            return existed, self._db.people()


# Shared singleton used across the app. Import this, not the class.
faceAPI = FaceAPI()
