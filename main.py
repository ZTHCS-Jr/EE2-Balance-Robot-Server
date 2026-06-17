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
from std_srvs.srv import SetBool

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
                await websocket.send_bytes(frame_bytes)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._ui_clients.discard(websocket)


manager = ConnectionManager()

# Autonomy control: the web buttons toggle the ROS enable services on the node below.
autonomy_state = "idle"   # "idle" | "mapping" | "seeking"
autonomy_node = None      # MapSubscriber instance; set once ros_spin_thread starts


async def handle_autonomy(mode) -> None:
    """Map a UI autonomy button to the /explore/enable and /seek/enable services.

    Mapping and seeking both drive /cmd_vel, so they are mutually exclusive: starting
    one disables the other. 'stop' disables both."""
    global autonomy_state
    if mode not in ("map", "seek", "stop"):
        return

    node = autonomy_node
    if node is None:
        await manager.broadcast_to_ui(json.dumps(
            {"type": "autonomy", "state": autonomy_state, "error": "ROS not ready"}))
        return

    if mode == "map":
        if not node.explore_ready():
            await manager.broadcast_to_ui(json.dumps(
                {"type": "autonomy", "state": "idle", "error": "explorer not running"}))
            return
        node.set_seek(False)
        node.set_explore(True)
        autonomy_state = "mapping"
    elif mode == "seek":
        if not node.seek_ready():
            await manager.broadcast_to_ui(json.dumps(
                {"type": "autonomy", "state": "idle", "error": "face seeker not running"}))
            return
        node.set_explore(False)
        node.set_seek(True)
        autonomy_state = "seeking"
    else:  # stop
        node.set_explore(False)
        node.set_seek(False)
        autonomy_state = "idle"

    await manager.broadcast_to_ui(json.dumps({"type": "autonomy", "state": autonomy_state}))


@app.websocket("/ws/ui")
async def websocket_ui(websocket: WebSocket) -> None:
    await manager.add_ui(websocket)
    # Sync the new client's buttons / joystick-lock to the current autonomy state.
    try:
        await websocket.send_text(json.dumps({"type": "autonomy", "state": autonomy_state}))
    except Exception:
        pass
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ptype = payload.get("type")

            if ptype == "autonomy":
                await handle_autonomy(payload.get("mode"))
                continue

            if ptype != "joystick":
                continue

            # Joystick is locked while autonomy is running (don't fight /cmd_vel).
            if autonomy_state != "idle":
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

        # Autonomy enable services (toggled by the web dashboard buttons).
        self.explore_cli = self.create_client(SetBool, '/explore/enable')
        self.seek_cli = self.create_client(SetBool, '/seek/enable')

    def explore_ready(self) -> bool:
        return self.explore_cli.service_is_ready()

    def seek_ready(self) -> bool:
        return self.seek_cli.service_is_ready()

    def _set_enable(self, cli, value: bool) -> None:
        req = SetBool.Request()
        req.data = bool(value)
        future = cli.call_async(req)

        def _done(fut):
            try:
                resp = fut.result()
                self.get_logger().info(f"[autonomy] enable={value}: ok={resp.success} {resp.message}")
            except Exception as exc:
                self.get_logger().warning(f"[autonomy] service call failed: {exc}")

        future.add_done_callback(_done)

    def set_explore(self, value: bool) -> None:
        self._set_enable(self.explore_cli, value)

    def set_seek(self, value: bool) -> None:
        self._set_enable(self.seek_cli, value)

    def marker_callback(self, msg):
        self.latest_markers = msg.markers
        # print to FastAPI terminal when face is found
        print(f"[Web UI] Received {len(self.latest_markers)} markers") 
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
        self.base_map_bgr = cv2.flip(img_color, 0)
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
            dynamic_scale_factor = max(1.0, (max(width, height) * 0.003))

            for marker in self.latest_markers:
                mx = marker.pose.position.x
                my = marker.pose.position.y
                
                gx = int((mx - origin_x) / resolution)
                gy = int((my - origin_y) / resolution)
                gy_flipped = height - 1 - gy

                if 0 <= gx < width and 0 <= gy_flipped < height:
                    b = int(marker.color.b * 255)
                    g = int(marker.color.g * 255)
                    r = int(marker.color.r * 255)
                    color = (b, g, r)

                    if marker.type == 2:  # circle
                        base_radius = int((marker.scale.x / 2.0) / resolution)
                        radius = int(max(2 * dynamic_scale_factor, base_radius))
                        cv2.circle(display_img, (gx, gy_flipped), radius, color, -1)
                        cv2.circle(display_img, (gx, gy_flipped), radius, (0, 0, 0), max(1, int(1 * dynamic_scale_factor)))

                    elif marker.type == 9:  # text
                        text = marker.text or "Unknown"
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        scale = 0.25 * dynamic_scale_factor  
                        thickness = max(1, int(1 * dynamic_scale_factor))
                        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
                        tx = gx - tw // 2
                        ty = gy_flipped - 8
                        
                        cv2.rectangle(display_img, (tx - 2, ty - th - 2), (tx + tw + 2, ty + 2), (0, 0, 0), -1)
                        cv2.putText(display_img, text, (tx, ty), font, scale, color, thickness)
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
    global autonomy_node
    rclpy.init()
    node = MapSubscriber()
    autonomy_node = node
    try:
        rclpy.spin(node)
    except Exception as e:
        print(f"[ROS 2] Map Node stopped: {e}")
    finally:
        node.destroy_node()
        rclpy.shutdown()

async def continuous_aws_sync():
    """Polls AWS DynamoDB at regular intervals with forced hardcoded fallback."""
    print("[AWS DB SYNC] Starting continuous background sync...")
    
    FORCED_KEY = "ASIAR5WSNIN245QIJ2NF"
    FORCED_SECRET = "y4C0+3JynBHSocxFRHXSN9KhYx46u4J/CBvrbd9E"
    FORCED_TOKEN = "IQoJb3JpZ2luX2VjELX//////////wEaCXVzLXdlc3QtMiJHMEUCID7Ip4QBM29NRiLYmHHLWXEF1oA3rKiwmMkHyQs+PjT3AiEApDAfslm6m83qk2z4nkPJcaBNE4f7V9WnKQvSc4zg/v4qtAIIfhAAGgwxMzI1MTE1MTU1MDkiDPgJqo+q1oSqAvoESSqRAoJ/DHwa0Tppz53T0CNj3rPxChVcB0KFO8MbbG1aUTV4rFheWQN02UIXV0yXjLLKUP8G5aNJHc/DtTo/TzAwilvDJssyhksParmIQFuDswS+Wy++VfrPfTkdlw0Sor+d0sib2eXGRuT2gtxjVFNnbifWVN+yxypG154UmuyeTIyICaVLcGIMMSMXxGEx6day9uEKZDhBSi0uUP8t1IKCHfnIJK4Vqj0IXahdA4cnRav2N1ol/716yBlAfPlGZ2CYbX7EvO5HZaQzNvYLW7FD5h5OAtJQHf9j0RDkWLT3VhJ6TLUOmE/mnrTLcHQSz88hh6zHFaKXRG3Z0dP3LAlBWC+PJhcxx615ExgAejDMc8p3eDCK98bRBjqdAb/0Yv4mSHo15KfU0Ih1s2WNo4rtKPbVgh1wR3R608phdIH/SGJwuYy5eVPKCcTENh0XpPvfObqukdR4ZN+UGtbxWtSaXVWt+HpLoCREGXQiet5opED5YLkxsqBFsBAR66ER2FdQq1fsJZSsSkbDeLmD6gYGnv/6oz4gwJAfQWlM6uHueM/6Ql4rqANmOAQPreTeKw3S4GqVqz2RZUw="

    is_first_sync = True
    synced_aws_keys = set()
    
    while True:
        try:
            # use hardcoded strings directly
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