from __future__ import annotations
import threading
import base64
import numpy as np
import cv2
import boto3
import os
import getpass

import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid
from visualization_msgs.msg import MarkerArray

import asyncio
import json
from typing import Set

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from face_api import faceAPI

app = FastAPI(title="Robot Registration MVP")

app.mount("/static", StaticFiles(directory="static"), name="static")

user_languages={}

@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


def clamp(value: float, min_value: float = -1.0, max_value: float = 1.0) -> float:
    return max(min_value, min(max_value, value))


def format_command(linear: float, angular: float) -> str:
    def fmt(value: float) -> str:
        if abs(value) < 1e-6:
            return "0"
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text if text else "0"

    return f"<{fmt(linear)},{fmt(angular)}>"


class ConnectionManager:
    def __init__(self) -> None:
        self._ui_clients: Set[WebSocket] = set()
        self._robot_clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add_ui(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._ui_clients.add(websocket)

    async def remove_ui(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._ui_clients.discard(websocket)

    async def add_robot(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._robot_clients.add(websocket)

    async def remove_robot(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._robot_clients.discard(websocket)

    async def ui_count(self) -> int:
        async with self._lock:
            return len(self._ui_clients)

    async def broadcast_to_robots(self, message: str) -> None:
        async with self._lock:
            targets = list(self._robot_clients)

        if not targets:
            return

        dead = []
        for websocket in targets:
            try:
                await websocket.send_text(message)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._robot_clients.discard(websocket)

    async def broadcast_to_ui(self, message: str) -> None:
        async with self._lock:
            targets = list(self._ui_clients)

        if not targets:
            return

        dead = []
        for websocket in targets:
            try:
                await websocket.send_text(message)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._ui_clients.discard(websocket)
                    
    async def broadcast_bytes_to_ui(self, frame_bytes: bytes) -> None:
        async with self._lock:
            targets = list(self._ui_clients)

        if not targets:
            return

        dead = []
        for websocket in targets:
            try:
                await websocket.send_bytes(frame_bytes)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._ui_clients.discard(websocket)


manager = ConnectionManager()


@app.websocket("/ws/ui")
async def websocket_ui(websocket: WebSocket) -> None:
    await manager.add_ui(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if payload.get("type") != "joystick":
                continue

            try:
                angular = float(payload.get("x", 0.0))
                linear = float(payload.get("y", 0.0))
            except (TypeError, ValueError):
                continue

            linear = clamp(linear)
            angular = clamp(angular)
            await manager.broadcast_to_robots(format_command(linear, angular))
    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove_ui(websocket)
        if await manager.ui_count() == 0:
            await manager.broadcast_to_robots(format_command(0.0, 0.0))


@app.websocket("/ws/robot")
async def websocket_robot(websocket: WebSocket) -> None:
    await manager.add_robot(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue

            if payload.get("type") != "telemetry":
                continue

            await manager.broadcast_to_ui(json.dumps(payload))
    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove_robot(websocket)


# the video endpoint now catches the bytes and uses the manager properly
_detect_logged_once = False


@app.websocket("/ws/video")
async def websocket_video(websocket: WebSocket) -> None:
    await websocket.accept()
    frame_idx = 0
    detect_busy = [False]
    try:
        while True:
            frame_bytes = await websocket.receive_bytes()

            await manager.broadcast_bytes_to_ui(frame_bytes)
            if frame_idx % config.DETECT_EVERY_N == 0 and not detect_busy[0]:
                detect_busy[0] = True
                asyncio.create_task(_run_detection(frame_bytes, frame_idx, detect_busy))
            frame_idx += 1
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[SERVER ERROR] Video socket dropped: {e}")


async def _run_detection(frame_bytes: bytes, frame_idx: int, busy_flag: list) -> None:
    global _detect_logged_once
    try:
        faces = await asyncio.to_thread(faceAPI.detect, frame_bytes)
        for face in faces:
            name = face.get('name')
            if name and name!="Unknown":
                face['language']=user_languages.get(name, "English")
            else:
                face['language']="English"

    except Exception as exc:
        print(f"[face_api] detect failed: {exc}")
        busy_flag[0] = False
        return
    if not _detect_logged_once:
        print(f"[face_api] first detection complete: {len(faces)} face(s) on frame {frame_idx}")
        _detect_logged_once = True
    try:
        await manager.broadcast_to_ui(json.dumps({
            "type": "detections",
            "frame_idx": frame_idx,
            "faces": faces,
        }))
    finally:
        busy_flag[0] = False


# Face recognition REST api
@app.post("/api/enroll")
async def api_enroll(name: str = Form(...), image: UploadFile = File(...)) -> dict:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    data = await image.read()
    ok, message, total = await asyncio.to_thread(faceAPI.enroll_image, name, data)
    if not ok:
        raise HTTPException(status_code=422, detail={"message": message, "count": total})
    return {"ok": True, "name": name, "count": total}


@app.get("/api/people")
async def api_people() -> dict:
    people = await asyncio.to_thread(faceAPI.list_people)
    return {"people": people, "init_error": faceAPI.init_error()}


@app.delete("/api/people/{name}")
async def api_delete_person(name: str) -> dict:
    existed, remaining = await asyncio.to_thread(faceAPI.delete_person, name)
    if not existed:
        raise HTTPException(status_code=404, detail="person not found")
    return {"ok": True, "remaining": remaining}

main_loop = None

class MapSubscriber(Node):
    def __init__(self):
        super().__init__('web_map_subscriber')
        self.subscription = self.create_subscription(OccupancyGrid, '/map', self.map_callback, 10)
        self.marker_sub = self.create_subscription(MarkerArray, '/people_markers', self.marker_callback, 10)
        
        self.latest_markers = []
        self.base_map_bgr = None
        self.map_info = None
        
        self.PAD_CELLS = 20 
        self.SCALE = 3 # Triple map resolution for text rendering

    def marker_callback(self, msg):
        self.latest_markers = msg.markers
        self.render_and_broadcast()

    def map_callback(self, msg):
        data = np.array(msg.data, dtype=np.int8)
        width, height = msg.info.width, msg.info.height
        self.map_info = msg.info

        grid = data.reshape((height, width))
        img = np.zeros((height, width), dtype=np.uint8)
        img[grid == -1] = 127
        img[grid == 0] = 255
        img[grid == 100] = 0
        img_color = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        img_color = cv2.flip(img_color, 0)

        # padding for names
        padded_img = cv2.copyMakeBorder(
            img_color, 
            self.PAD_CELLS, self.PAD_CELLS, self.PAD_CELLS, self.PAD_CELLS, 
            cv2.BORDER_CONSTANT, value=(127, 127, 127)
        )

        # upscale resolution 3x
        new_h, new_w = padded_img.shape[:2]
        high_res_map = cv2.resize(
            padded_img, 
            (new_w * self.SCALE, new_h * self.SCALE), 
            interpolation=cv2.INTER_NEAREST
        )

        self.base_map_bgr = high_res_map
        self.render_and_broadcast()

    def render_and_broadcast(self):
        if self.base_map_bgr is None or self.map_info is None:
            return

        display_img = self.base_map_bgr.copy()
        
        try:
            width = self.map_info.width
            height = self.map_info.height
            resolution = self.map_info.resolution
            origin_x = self.map_info.origin.position.x
            origin_y = self.map_info.origin.position.y

            for marker in self.latest_markers:
                mx = marker.pose.position.x
                my = marker.pose.position.y
                
                # raw cell coordinates
                gx_raw = (mx - origin_x) / resolution
                gy_raw = (my - origin_y) / resolution
                
                # shift by padding
                gx_padded = gx_raw + self.PAD_CELLS
                gy_padded = gy_raw + self.PAD_CELLS
                
                # apply the Y-Flip (accounting for the new padded height)
                padded_height = height + (2 * self.PAD_CELLS)
                gy_flipped = padded_height - 1 - gy_padded
                
                # multiply by our 3x Scale to get high-res pixel target
                gx = int(gx_padded * self.SCALE)
                gy = int(gy_flipped * self.SCALE)

                # Safe bounds checking
                img_h, img_w = display_img.shape[:2]
                if 0 <= gx < img_w and 0 <= gy < img_h:
                    b = int(marker.color.b * 255)
                    g = int(marker.color.g * 255)
                    r = int(marker.color.r * 255)
                    color = (b, g, r)

                    if marker.type == 2:  # SPHERE
                        # Radius scales perfectly with the high-res map
                        base_radius_cells = (marker.scale.x / 2.0) / resolution
                        radius = int(base_radius_cells * self.SCALE)
                        radius = max(2, radius)
                        
                        cv2.circle(display_img, (gx, gy), radius, color, -1)
                        cv2.circle(display_img, (gx, gy), radius, (0, 0, 0), 2)

                    elif marker.type == 9:  # TEXT
                        text = marker.text or "Unknown"
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        scale = 0.3
                        thickness = 1
                        
                        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
                        tx = gx - tw // 2
                        ty = gy - 12 
                        
                        cv2.rectangle(display_img, (tx - 2, ty - th - 2), (tx + tw + 2, ty + 2), (0, 0, 0), -1)
                        cv2.putText(display_img, text, (tx, ty), font, scale, color, thickness, cv2.LINE_AA)
                        
        except Exception as e:
            print(f"[Web UI] Error drawing markers: {e}")

        _, buffer = cv2.imencode('.png', display_img)
        b64_str = base64.b64encode(buffer).decode('utf-8')

        payload = {
            "type": "map",
            "image": f"data:image/png;base64,{b64_str}"
        }

        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                manager.broadcast_to_ui(json.dumps(payload)),
                main_loop
            )

def ros_spin_thread():
    rclpy.init()
    node = MapSubscriber()
    try:
        rclpy.spin(node)
    except Exception as e:
        print(f"[ROS 2] Map Node stopped: {e}")
    finally:
        node.destroy_node()
        rclpy.shutdown()

async def continuous_aws_sync():
    """Polls AWS DynamoDB at regular intervals with forced hardcoded fallback"""
    print("[AWS DB SYNC] Starting continuous background sync")
    
    FORCED_KEY = "ASIAR5WSNIN25FU5ABTX"
    FORCED_SECRET = "OX5mPzfD6kj911vbVuooGisgVS0kEDM5tH6ScLOZ"
    FORCED_TOKEN = "IQoJb3JpZ2luX2VjENn//////////wEaCXVzLXdlc3QtMiJGMEQCIH2fQrLwPbGx3Sa/lQhpn8AhZjzZUfGbrxZtZWdaDo+XAiBWuPVhzTVbDT5t0k+EnGT8kYrk83v3kz6LITb3NjzoaCq9Agii//////////8BEAAaDDEzMjUxMTUxNTUwOSIMTa3zz0FjOGM9MR1FKpECecNJZzNdz6iCoXO5yJ2A0dZDmgWmYXBEJR+XpIlfexKeFBC2VDVIoHoagWAMXgW+sZxTqebs0oRoEewAAE/skkOVcG7NBQ50pYO2RkBP7eWV0mcqpTBAvyoNra5fkYVeWNQrSBiPMuGPWbiGwi7aW4erEpegeK64slnjh4NdCwcUQW5NgD8sbv8irApY7zVBhXN0+I5ePvSVzwVSBtFGgn4wwxd9xfzo6S2VTsPM7mFE3n6Xp52YWVaiDcNKie4kHsz34wTPcqMpRsIax087QdbuIU6kc15nqWwtTPsE1jlKLbOfnYGkL81Vpq5VYpqBOoZzW3zi4hGkacfGwSWcA4AhaknG31xovfzvGhXI4OtjMPrcztEGOp4B1J6XrVPhmnexg5fGIOI2SSl8NqurCv+mzTmLAnc6Op5Hj6FcrwBmPDSGbjQ7zS+vhziBtiNKD9PcA29F8/g33LZn1blDJGa1UOmvU3CpWsHXnx/BDWDroZDzGB7BmRW2BgVB98a+Cob+H8AWQFHCilEoy9F0y/RAvDcywHO9wiE4QFSvVKuBaW+DXmwtypqTvocO0uZb1uBT/AXBu4U="

    is_first_sync = True
    synced_aws_keys = set()
    
    while True:
        try:
            # use hardcoded strings
            dynamodb = boto3.resource(
                'dynamodb',
                region_name='us-east-1',
                aws_access_key_id=FORCED_KEY,
                aws_secret_access_key=FORCED_SECRET,
                aws_session_token=FORCED_TOKEN
            )
                
            table = dynamodb.Table('Attendees')
            
            # Fetch data off thread
            response = await asyncio.to_thread(table.scan)
            items = response.get('Items', [])
            
            with faceAPI._lock:
                if faceAPI._db is not None:
                    if is_first_sync:
                        faceAPI._db.embeddings = np.empty((0, 512), dtype=np.float32)
                        faceAPI._db.labels = []
                        is_first_sync = False
                        
                    db_changed = False
                    current_aws_keys = {item['Name'] for item in items}

                    delete_keys = synced_aws_keys - current_aws_keys
                    delete_base_names = {k.rsplit('-', 1)[0] for k in delete_keys}
                    
                    for base_name in delete_base_names:
                        faceAPI._db.remove(base_name)
                        user_languages.pop(base_name,None)
                        print(f"[AWS DB SYNC] Deleted user from local memory: {base_name}")
                        db_changed = True
                    
                    for hanging_key in delete_keys:
                        synced_aws_keys.remove(hanging_key)

                    for item in items:
                        aws_key_name = item['Name']
                        if aws_key_name not in synced_aws_keys:
                            base_name = aws_key_name.rsplit('-', 1)[0]
                            embedding = [float(x) for x in item['Embedding']]
                            faceAPI._db.add(base_name, embedding)
                            synced_aws_keys.add(aws_key_name)
                            user_languages[base_name]=item.get('Language', 'English')
                            db_changed = True
                            print(f"[AWS DB SYNC] Downloaded new embedding: {aws_key_name}")
                            
                    if db_changed:
                        faceAPI._db.save()
                        print("[AWS DB SYNC] Local face_db.pkl updated successfully")
                        
        except Exception as e:
            print(f"[AWS DB SYNC] Error, failed to pull from AWS: {e}")
            
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    threading.Thread(target=ros_spin_thread, daemon=True).start()
    asyncio.create_task(continuous_aws_sync())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)