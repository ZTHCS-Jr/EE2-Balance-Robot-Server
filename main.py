from __future__ import annotations

import asyncio
import json
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Robot Registration MVP")

# Static frontend assets live in /static.
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

    # the video byte broadcaster is now correctly inside the ConnectionManager class!
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
@app.websocket("/ws/video")
async def websocket_video(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            # Receive raw binary JPEG bytes from the Pi
            frame_bytes = await websocket.receive_bytes()
            
            # Instantly broadcast those bytes to the UI browser connection
            await manager.broadcast_bytes_to_ui(frame_bytes)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[SERVER ERROR] Video socket dropped: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)