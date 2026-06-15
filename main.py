from __future__ import annotations
import threading
import base64
import numpy as np
import cv2
import boto3

import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid


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
                # Send as binary frame directly to the browser
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
    except Exception as exc:  # noqa: BLE001
        print(f"[face_api] detect failed: {exc}")
        busy_flag[0] = False
        return
    finally:
        # Always clear the flag, even if broadcast below raises, so detection
        # keeps firing on subsequent frames.
        pass
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
        # subscribe to map topic
        self.subscription = self.create_subscription(OccupancyGrid, '/map', self.map_callback, 10)

    def map_callback(self, msg):
        # connvert the ROS array to a 2D numpy array
        data = np.array(msg.data, dtype=np.int8)
        width, height = msg.info.width, msg.info.height
        grid = data.reshape((height, width))

        # Map values to greyscale colors
        # -1 (Unknown) -> 127 (Grey)
        # 0 (Free Space) -> 255 (White)
        # 100 (Wall/Occupied) -> 0 (Black)
        img = np.zeros((height, width), dtype=np.uint8)
        img[grid == -1] = 127
        img[grid == 0] = 255
        img[grid == 100] = 0

        # orientate SLAM map
        img = cv2.flip(img, 0)

        # compress into a PNG
        _, buffer = cv2.imencode('.png', img)
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
    """Polls AWS DynamoDB at regular intervals to sync attendees to the local robot"""
    print("[AWS DB SYNC] Start continuous background sync")
    
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    table = dynamodb.Table('Attendees')
    
    is_first_sync = True
    synced_aws_keys = set()
    
    while True:
        try:
            # table.scan() is blocking, so we run it in a thread to keep FastAPI fast
            response = await asyncio.to_thread(table.scan)
            items = response.get('Items', [])
            
            with faceAPI._lock:
                if faceAPI._db is not None:
                    # clear local cache on first boot
                    if is_first_sync:
                        faceAPI._db.embeddings = np.empty((0, 512), dtype=np.float32)
                        faceAPI._db.labels = []
                        is_first_sync = False
                        
                    db_changed=False
                    current_aws_keys={item['Name'] for item in items}

                    delete_keys=synced_aws_keys-current_aws_keys
                    delete_base_names={k.rsplit('-',1)[0] for k in delete_keys}
                    
                    for base_name in delete_base_names:
                        faceAPI._db.remove(base_name)
                        print(f"[AWS DB SYNC] deleted user from local memory: {base_name}")
                        db_changed=True
                    
                    for hanging_key in delete_keys:
                        synced_aws_keys.remove(hanging_key)

                    for item in items:
                        aws_key = item['Name']
                        if aws_key not in synced_aws_keys:
                            # strip suffix to obtain name
                            base_name = aws_key.rsplit('-', 1)[0]
                            # convert AWS decimals back to standard floats
                            embedding = [float(x) for x in item['Embedding']]
                            faceAPI._db.add(base_name, embedding)
                            synced_aws_keys.add(aws_key)
                            db_changed = True
                            print(f"[AWS DB SYNC] Downloaded new embedding: {aws_key}")
                            
                    if db_changed:
                        faceAPI._db.save()
                        print("[AWS DB SYNC] Local face_db.pkl updated")
                        
        except Exception as e:
            print(f"[AWS DB SYNC] Error! Failed to pull from AWS: {e}")
            
        # Wait 10 seconds before sync
        await asyncio.sleep(10)

# start node in background when FastAPI boots
@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    threading.Thread(target=ros_spin_thread, daemon=True).start()
    asyncio.create_task(continuous_aws_sync())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)