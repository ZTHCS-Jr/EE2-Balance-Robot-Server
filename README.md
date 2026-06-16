# EE2-Balance-Robot-Server

Server for the self-balancing robot. A FastAPI app that serves a web
dashboard for **driving the robot** (virtual joystick), **viewing the Pi camera**, and
**face recognition** (live identification + enrolment), while relaying telemetry back
from the robot. Runs on the laptop; the browser, the robot's Raspberry Pi, and the ROS
face-seeker all connect to it.

## Features

* **Drive control**: virtual joystick (nipplejs) → normalised `<linear,angular>`
  commands relayed to the robot.
* **Live video**: raw JPEG frames from the Pi camera streamed straight to the browser.
* **Face recognition**: InsightFace (`buffalo_l`: SCRFD detector + ArcFace embeddings)
  identifies faces in the video stream and overlays boxes (green = known, red = unknown).
* **Enrolment**: capture faces from the laptop webcam and register them by name.
* **Telemetry**: battery, power, IMU angle and last command shown as live gauges.

## Install


```bash
pip install -r requirements.txt
```

This pulls FastAPI/uvicorn plus the face stack (`insightface`, `onnxruntime`,
`opencv-python`). **First run downloads the `buffalo_l` model (~300 MB)**

## Run

```bash
python main.py          # http://0.0.0.0:8000
```


## Architecture

`main.py` is the whole server. Endpoints:

| Endpoint | Who connects | Purpose |
|---|---|---|
| `GET /` | browser | Serves the dashboard (`static/index.html`). |
| `WS /ws/ui` | browser | Joystick in → `<linear,angular>` relayed to the robot; telemetry / detections / video pushed out. |
| `WS /ws/robot` | Pi client | Robot pushes `telemetry`; relayed to all UI clients. |
| `WS /ws/video` | Pi camera | Raw JPEG frames in → broadcast to UI + face detection every `DETECT_EVERY_N` frames. |
| `POST /api/enroll` | browser | Register a face (`name` + image) into the gallery. |
| `GET /api/people` | browser | List enrolled people and sample counts. |
| `DELETE /api/people/{name}` | browser | Remove a person's embeddings. |

**Drive command format:** the joystick sends `{type:"joystick", x, y}`; the server maps
`x → angular`, `y → linear`, clamps both to `[-1, 1]`, and broadcasts a string like
`<0.5,-0.2>` to robot clients. When the last UI client disconnects it sends `<0,0>`.

### Module map

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, WebSocket relay (`ConnectionManager`), REST enrolment API. |
| `face_api.py` | Loads InsightFace; `detect()` / `enroll_image()`|
| `face_db.py` | Embedding gallery (`face_db.pkl`); cosine-similarity matching. |
| `config.py` | Model + recognition settings (see below). |
| `static/` | Frontend (`index.html`, `app.js`, `style.css`) - React + nipplejs via CDN |

## Configuration

Recognition settings live in `config.py`:

| Setting | Default | Notes |
|---|---|---|
| `MODEL_NAME` | `buffalo_l` | `buffalo_s` is lighter/faster on CPU but less accurate. |
| `DET_SIZE` | `(320, 320)` | Detector input; smaller = faster but misses small/distant faces. |
| `MATCH_THRESHOLD` | `0.5` | Cosine-similarity cutoff for declaring a match. |
| `DETECT_EVERY_N` | `1` | Run detection every Nth video frame to save CPU. |
| `DB_PATH` | `face_db.pkl` | Where enrolled embeddings are stored. |

