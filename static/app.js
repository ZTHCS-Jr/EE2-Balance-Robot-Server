(() => {
  const e = React.createElement;
  const SEND_INTERVAL_MS = 40;
  const JOYSTICK_SIZE = 180;
  const WS_PATH = "/ws/ui";
  const NIPPLE_SOURCES = [
    "https://cdn.jsdelivr.net/npm/nipplejs@0.9.0/dist/nipplejs.min.js",
    "https://unpkg.com/nipplejs@0.9.0/dist/nipplejs.min.js",
    "/static/vendor/nipplejs.min.js",
  ];

  const loadScript = (src) =>
    new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = () => resolve(true);
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });

  const loadNipple = async () => {
    if (window.nipplejs) {
      return true;
    }
    for (const src of NIPPLE_SOURCES) {
      try {
        await loadScript(src);
      } catch (error) {
        continue;
      }
      if (window.nipplejs) {
        return true;
      }
    }
    return false;
  };

  const clamp = (value, min = -1, max = 1) => Math.max(min, Math.min(max, value));

  const normalizeTelemetry = (payload) => ({
    type: "telemetry",
    timestamp: typeof payload.timestamp === "number" ? payload.timestamp : Date.now(),
    battery_capacity: Number.isFinite(payload.battery_capacity) ? payload.battery_capacity : 0,
    power_consumption: Number.isFinite(payload.power_consumption) ? payload.power_consumption : 0,
    imu_angle: Number.isFinite(payload.imu_angle) ? payload.imu_angle : 0,
    last_linear: Number.isFinite(payload.last_linear) ? payload.last_linear : 0,
    last_angular: Number.isFinite(payload.last_angular) ? payload.last_angular : 0,
  });

  const ANGLES = [
    { key: "front", label: "Front" },
    { key: "left", label: "Left" },
    { key: "right", label: "Right" },
  ];

  function App() {
    const [connected, setConnected] = React.useState(false);
    const [joystickStatus, setJoystickStatus] = React.useState("loading");
    const [activeTab, setActiveTab] = React.useState("vision");
    const [hasVideoStream, setHasVideoStream] = React.useState(false);
    const [detections, setDetections] = React.useState([]);
    const [enrolName, setEnrolName] = React.useState("");
    const [angleCounts, setAngleCounts] = React.useState({ front: 0, left: 0, right: 0 });
    const [people, setPeople] = React.useState([]);
    const [enrolStatus, setEnrolStatus] = React.useState({ kind: "idle", text: "" });
    const [webcamStatus, setWebcamStatus] = React.useState("idle");
    const [telemetry, setTelemetry] = React.useState({
      type: "telemetry",
      timestamp: Date.now(),
      battery_capacity: 85.5,
      power_consumption: 12.4,
      imu_angle: 0.2,
      last_linear: 0,
      last_angular: 0,
    });
    const [mapSrc, setMapSrc]=React.useState(null);
    const [autonomyState, setAutonomyState] = React.useState("idle"); // "idle" | "mapping" | "seeking"
    const [autonomyError, setAutonomyError] = React.useState("");

    const joystickRef = React.useRef(null);
    const wsRef = React.useRef(null);
    const videoImgRef = React.useRef(null);
    const webcamVideoRef = React.useRef(null);
    const captureCanvasRef = React.useRef(null);
    const webcamStreamRef = React.useRef(null);
    const latestRef = React.useRef({ x: 0, y: 0 });
    const sendTimerRef = React.useRef(null);
    const reconnectRef = React.useRef({ timer: null, attempts: 0 });
    const autonomyRef = React.useRef("idle");  // mirror of autonomyState for the joystick closure

    const sendPayload = React.useCallback((payload) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
      }
    }, []);

    const refreshPeople = React.useCallback(async () => {
      try {
        const response = await fetch("/api/people");
        if (!response.ok) return;
        const data = await response.json();
        if (Array.isArray(data.people)) {
          setPeople(data.people);
        }
      } catch (err) {
        // ignore - panel just shows empty
      }
    }, []);

    React.useEffect(() => {
      let cancelled = false;
      loadNipple().then((ready) => {
        if (cancelled) return;
        setJoystickStatus(ready ? "ready" : "missing");
        if (!ready) console.warn("Nipple.js failed to load.");
      });
      return () => { cancelled = true; };
    }, []);

    React.useEffect(() => {
      const scheduleReconnect = () => {
        if (reconnectRef.current.timer) {
          return;
        }
        reconnectRef.current.attempts += 1;
        const delay = Math.min(1000 * 2 ** reconnectRef.current.attempts, 8000);
        reconnectRef.current.timer = window.setTimeout(() => {
          reconnectRef.current.timer = null;
          connect();
        }, delay);
      };

      const connect = () => {
        const scheme = window.location.protocol === "https:" ? "wss" : "ws";
        const url = `${scheme}://${window.location.host}${WS_PATH}`;
        const ws = new WebSocket(url);
        ws.binaryType = "blob";
        wsRef.current = ws;

        ws.addEventListener("open", () => {
          setConnected(true);
          reconnectRef.current.attempts = 0;
        });

        ws.addEventListener("message", (event) => {
          if (!event.data) return;

          if (event.data instanceof Blob) {
            if (videoImgRef.current) {
              setHasVideoStream(true);
              const url = URL.createObjectURL(event.data);
              const oldUrl = videoImgRef.current.src;

              videoImgRef.current.src = url;

              if (oldUrl && oldUrl.startsWith("blob:")) {
                URL.revokeObjectURL(oldUrl);
              }
            }
            return;
          }

          try {
            const payload = JSON.parse(event.data);
            if (!payload || !payload.type) return;
            if (payload.type === "telemetry") {
              setTelemetry(normalizeTelemetry(payload));
            } else if (payload.type === "detections") {
              setDetections(Array.isArray(payload.faces) ? payload.faces : []);
            }
            else if (payload.type === "map"){
              setMapSrc(payload.image);
            }
            else if (payload.type === "autonomy") {
              setAutonomyState(payload.state || "idle");
              setAutonomyError(typeof payload.error === "string" ? payload.error : "");
            }
          } catch (error) {
            return;
          }
        });

        ws.addEventListener("close", () => {
          setConnected(false);
          setHasVideoStream(false);
          setDetections([]);
          scheduleReconnect();
        });

        ws.addEventListener("error", () => {
          ws.close();
        });
      };

      connect();

      return () => {
        if (reconnectRef.current.timer) {
          clearTimeout(reconnectRef.current.timer);
        }
        if (wsRef.current) {
          wsRef.current.close();
        }
      };
    }, []);

    React.useEffect(() => {
      if (!joystickRef.current || joystickStatus !== "ready" || !window.nipplejs) {
        return;
      }

      const maxRadius = JOYSTICK_SIZE / 2;
      const joystick = window.nipplejs.create({
        zone: joystickRef.current,
        mode: "static",
        position: { left: "50%", top: "50%" },
        size: JOYSTICK_SIZE,
        color: "#2b7a78",
        restOpacity: 0.8,
      });

      const sendLatest = () => {
        if (autonomyRef.current !== "idle") return;   // joystick locked while autonomy runs
        const { x, y } = latestRef.current;
        sendPayload({ type: "joystick", x, y });
      };

      const startSending = () => {
        if (autonomyRef.current !== "idle") return;    // joystick locked while autonomy runs
        if (sendTimerRef.current) {
          return;
        }
        sendTimerRef.current = window.setInterval(sendLatest, SEND_INTERVAL_MS);
      };

      const stopSending = () => {
        if (sendTimerRef.current) {
          clearInterval(sendTimerRef.current);
          sendTimerRef.current = null;
        }
      };

      const handleMove = (_event, data) => {
        if (!data || !data.vector) return;
        const force = Math.min(data.distance / maxRadius, 1);
        const angular = clamp(data.vector.x * force, -1, 1);  // nipplejs x is +right; negate so push-left = turn-left (CCW, REP-103)
        const linear = clamp(-data.vector.y * force, -1, 1);
        latestRef.current = { x: angular, y: linear };
      };

      const handleEnd = () => {
        latestRef.current = { x: 0, y: 0 };
        sendLatest();
        stopSending();
      };

      joystick.on("start", startSending);
      joystick.on("move", handleMove);
      joystick.on("end", handleEnd);

      return () => {
        joystick.destroy();
        stopSending();
      };
    }, [joystickStatus, sendPayload]);

    React.useEffect(() => {
      const interval = window.setInterval(() => {
        setTelemetry((current) => ({ ...current, timestamp: Date.now() }));
      }, 1000);
      return () => window.clearInterval(interval);
    }, []);

    // Keep the joystick closure's view of autonomy current, and when autonomy turns on,
    // stop any active command stream and clear the last vector so nothing stale is sent
    // when it later releases.
    React.useEffect(() => {
      autonomyRef.current = autonomyState;
      if (autonomyState !== "idle") {
        if (sendTimerRef.current) {
          clearInterval(sendTimerRef.current);
          sendTimerRef.current = null;
        }
        latestRef.current = { x: 0, y: 0 };
      }
    }, [autonomyState]);

    // Start / stop the laptop webcam stream when the Face Recognition tab is active.
    React.useEffect(() => {
      if (activeTab !== "face") {
        if (webcamStreamRef.current) {
          webcamStreamRef.current.getTracks().forEach((t) => t.stop());
          webcamStreamRef.current = null;
        }
        setWebcamStatus("idle");
        return undefined;
      }
      let cancelled = false;
      setWebcamStatus("loading");
      navigator.mediaDevices
        .getUserMedia({ video: { width: 640, height: 480 }, audio: false })
        .then((stream) => {
          if (cancelled) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          webcamStreamRef.current = stream;
          if (webcamVideoRef.current) {
            webcamVideoRef.current.srcObject = stream;
          }
          setWebcamStatus("ready");
        })
        .catch((err) => {
          console.warn("getUserMedia failed:", err);
          if (!cancelled) setWebcamStatus("denied");
        });
      return () => {
        cancelled = true;
      };
    }, [activeTab]);

    // Refresh the saved-people list when entering the Face tab.
    React.useEffect(() => {
      if (activeTab === "face") {
        refreshPeople();
      }
    }, [activeTab, refreshPeople]);

    const captureAngle = React.useCallback(
      async (angleKey) => {
        const name = enrolName.trim();
        if (!name) {
          setEnrolStatus({ kind: "error", text: "Enter a name first." });
          return;
        }
        const video = webcamVideoRef.current;
        const canvas = captureCanvasRef.current;
        if (!video || !canvas || video.videoWidth === 0) {
          setEnrolStatus({ kind: "error", text: "Webcam not ready yet." });
          return;
        }
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        
        // Convert canvas directly to Base64
        const base64Image = canvas.toDataURL("image/jpeg", 0.92);

        setEnrolStatus({ kind: "info", text: "Upload to AWS DB" });
        
        try {
          const API_KEY = "harrisonjr12"; 
          const AWS_API_URL = "https://iwt4lg01s6.execute-api.us-east-1.amazonaws.com/enroll";

          const response = await fetch(AWS_API_URL, { 
            method: "POST", 
            headers: { 
              "Content-Type": "application/json",
              "robot-api-key": API_KEY
            },
            body: JSON.stringify({
              name: `${name}-${angleKey}`, 
              image: base64Image
            })
          });

          if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            const msg = detail?.message || `AWS Enrol failed (${response.status})`;
            setEnrolStatus({ kind: "error", text: String(msg) });
            return;
          }
          
          setAngleCounts((current) => ({ ...current, [angleKey]: current[angleKey] + 1 }));
          setEnrolStatus({ kind: "ok", text: `Saved ${angleKey} for ${name} to AWS DB` });
          
          setTimeout(refreshPeople, 1000);
        } catch (err) {
          setEnrolStatus({ kind: "error", text: "Network error connecting to AWS" });
        }
      },
      [enrolName, refreshPeople]
    ); 

    const deletePerson = React.useCallback(
      async (name) => {
        if (!window.confirm(`Delete all enrolment data for ${name} from database?`)) return;
        
        try {
          const API_KEY = "harrisonjr12"; 
          const AWS_API_URL = "https://iwt4lg01s6.execute-api.us-east-1.amazonaws.com/enroll";
          
          const response = await fetch(AWS_API_URL, {
            method: "DELETE",
            headers: { 
              "Content-Type": "application/json",
              "robot-api-key": API_KEY
            },
            body: JSON.stringify({ name: name })
          });
          
          if (!response.ok) {
            setEnrolStatus({ kind: "error", text: `Delete failed (${response.status})` });
            return;
          }
          
          setEnrolStatus({ kind: "ok", text: `Removed ${name} from database` });
          if (enrolName.trim() === name) {
            setAngleCounts({ front: 0, left: 0, right: 0 });
          }
          setPeople((currentPeople)=>currentPeople.filter((p)=>p.name!==name));
          setTimeout(refreshPeople, 12000);
        } catch (err) {
          setEnrolStatus({ kind: "error", text: "Network error during delete" });
        }
      },
      [enrolName, refreshPeople]
    );

    const renderTabButton = (id, label) =>
      e(
        "button",
        {
          type: "button",
          className: activeTab === id ? "tab-button active" : "tab-button",
          onClick: () => setActiveTab(id),
        },
        label
      );

    const telemetryJson = JSON.stringify(telemetry, null, 2);

    const showVideo = activeTab === "slam" || activeTab === "face";

    const enrolPanel = e(
      "div",
      { className: "enrol-panel" },
      e(
        "div",
        { className: "enrol-grid" },
        e(
          "div",
          { className: "enrol-camera" },
          e("video", {
            ref: webcamVideoRef,
            autoPlay: true,
            muted: true,
            playsInline: true,
            className: "enrol-video",
          }),
          e("canvas", { ref: captureCanvasRef, style: { display: "none" } }),
          e(
            "div",
            { className: "enrol-camera-status" },
            webcamStatus === "loading" && "Requesting webcam...",
            webcamStatus === "denied" && "Webcam permission denied or unavailable",
            webcamStatus === "ready" && "Look at the laptop webcam, then capture each angle"
          )
        ),
        e(
          "div",
          { className: "enrol-controls" },
          e(
            "label",
            { className: "enrol-field" },
            e("span", null, "Name"),
            e("input", {
              type: "text",
              value: enrolName,
              onChange: (ev) => setEnrolName(ev.target.value),
              placeholder: "e.g. Alice",
            })
          ),
          e(
            "div",
            { className: "enrol-angles" },
            ANGLES.map((a) =>
              e(
                "button",
                {
                  key: a.key,
                  type: "button",
                  className: "enrol-button",
                  onClick: () => captureAngle(a.key),
                  disabled: webcamStatus !== "ready",
                },
                `Capture ${a.label}`,
                e("span", { className: "enrol-count" }, angleCounts[a.key])
              )
            )
          ),
          enrolStatus.text
            ? e("div", { className: `enrol-status ${enrolStatus.kind}` }, enrolStatus.text)
            : null
        )
      ),
      e(
        "div",
        { className: "people-list" },
        e("div", { className: "people-title" }, "Saved people"),
        people.length === 0
          ? e("div", { className: "people-empty" }, "No one enrolled yet.")
          : e(
              "ul",
              { className: "people-items" },
              people.map((p) =>
                e(
                  "li",
                  { key: p.name },
                  e("span", { className: "people-name" }, p.name),
                  e("span", { className: "people-count" }, `${p.count} sample${p.count === 1 ? "" : "s"}`),
                  e(
                    "button",
                    {
                      type: "button",
                      className: "people-delete",
                      onClick: () => deletePerson(p.name),
                    },
                    "Delete"
                  )
                )
              )
            )
      )
    );

    const getBatteryColor = (soc)=>{
      if (soc<=25) return "var(--danger)";
      if (soc<=50) return "#f59f00"
      return "var(--accent)";
    };
    const batteryColor=getBatteryColor(telemetry.battery_capacity);

    const showVision = activeTab === "vision" || activeTab === "face";
    const showSlam = activeTab === "slam";
    const showTelemetry = activeTab === "telemetry";

    const visionView = e(
      "div",
      { className: "stage-content", style: { display: showVision ? "flex" : "none" } },
      e(
        "div",
        { className: "stream-container" },
        e("img", {
          ref: videoImgRef,
          className: "stream-media",
          style: { display: hasVideoStream ? "block" : "none" },
          alt: "Pi Camera Feed",
        }),
        activeTab === "face" && hasVideoStream && videoImgRef.current
          ? e(
              "svg",
              {
                className: "detection-overlay",
                viewBox: `0 0 ${videoImgRef.current.naturalWidth || 1} ${videoImgRef.current.naturalHeight || 1}`,
                preserveAspectRatio: "xMidYMid meet",
              },
              detections.map((det, idx) => {
                const [x1, y1, x2, y2] = det.bbox;
                const matched = det.name !== null && det.name !== undefined;
                const colour = matched ? "#2e9b59" : "#d64545";
                const label = matched
                  ? `${det.name} (${det.score.toFixed(2)})`
                  : `Unknown (${det.score.toFixed(2)})`;
                return e(
                  "g",
                  { key: idx },
                  e("rect", {
                    x: x1, 
                    y: y1, 
                    width: Math.max(1, x2 - x1), 
                    height: Math.max(1, y2 - y1), 
                    fill: "none", stroke: colour, 
                    strokeWidth: 3 }),
                  e(
                    "text", 
                    { 
                      x: x1, 
                      y: Math.max(y1 - 6, 14), 
                      fill: colour, 
                      fontSize: 18, 
                      fontWeight: 600, 
                      className: "detection-text" 
                    }, 
                    label
                  )
                );
              })
            )
          : null,
        !hasVideoStream ? e("div", { className: "slam-placeholder" }, "Camera Offline") : null
      ),
      activeTab === "face" ? enrolPanel : null
    );

    const slamView = e(
      "div",
      { className: "stage-content", style: { display: showSlam ? "flex" : "none" } },
      e(
        "div",
        { className: "stream-container" },
        mapSrc
          ? e("img", { src: mapSrc, className: "stream-media slam-map-image" })
          : e("div", { className: "slam-placeholder" }, "Waiting for /map topic")
      )
    );

    const telemetryView = e(
      "div",
      { className: "telemetry", style: { display: showTelemetry ? "flex" : "none" } },
      e(
        "div",
        { className: "telemetry-grid" },
        e("div", { className: "gauge-card" },
          e("div", { className: "gauge-label" }, "Battery"),
          e("div", { className: "gauge", style: { "--value": Math.max(0, Math.min(100, telemetry.battery_capacity)), "--gauge-color": batteryColor } },
            e("div", { className: "gauge-value" }, `${telemetry.battery_capacity.toFixed(1)}%`)
          )
        ),
        e("div", { className: "gauge-card" },
          e("div", { className: "gauge-label" }, "Power"),
          e("div", { className: "gauge", style: { "--value": Math.max(0, Math.min(100, telemetry.power_consumption * 5)) } },
            e("div", { className: "gauge-value" }, `${telemetry.power_consumption.toFixed(1)} W`)
          )
        ),
        e("div", { className: "gauge-card" },
          e("div", { className: "gauge-label" }, "IMU Tilt Angle"),
          e("div", { className: "gauge", style: { "--value": Math.max(0, Math.min(100, (telemetry.imu_angle + 1) * 50)) } },
            e("div", { className: "gauge-value" }, `${telemetry.imu_angle.toFixed(2)} rad`)
          )
        ),
        e("div", { className: "gauge-card" },
          e("div", { className: "gauge-label" }, "Velocity"),
          e("div", { className: "gauge", style: { "--value": Math.max(0, Math.min(100, Math.abs(telemetry.last_linear) * 100)) } },
            e("div", { className: "gauge-value" }, `${telemetry.last_linear.toFixed(2)} m/s`)
          )
        )
      ),
      e(
        "div",
        { className: "telemetry-json" },
        e("div", { className: "telemetry-title" }, "Raw Telemetry"),
        e("pre", { className: "telemetry-code" }, telemetryJson)
      )
    );

    const stageContent = e(React.Fragment, null, visionView, slamView, telemetryView);

    const autonomyActive = autonomyState !== "idle";
    const autonomyControls = e(
      "div",
      { className: "autonomy-controls" },
      autonomyActive
        ? e(
            "button",
            {
              type: "button",
              className: "autonomy-button stop",
              onClick: () => sendPayload({ type: "autonomy", mode: "stop" }),
            },
            autonomyState === "mapping" ? "Stop Mapping" : "Stop Face Seeking"
          )
        : e(
            React.Fragment,
            null,
            e(
              "button",
              {
                type: "button",
                className: "autonomy-button",
                onClick: () => sendPayload({ type: "autonomy", mode: "map" }),
              },
              "Start Mapping"
            ),
            e(
              "button",
              {
                type: "button",
                className: "autonomy-button",
                onClick: () => sendPayload({ type: "autonomy", mode: "seek" }),
              },
              "Start Face Seeking"
            )
          ),
      autonomyError ? e("div", { className: "autonomy-error" }, autonomyError) : null
    );

    return e(
      "div",
      { className: "app" },
      e(
        "header",
        { className: "header" },
        e(
          "div",
          { className: "title-group" },
          e("h1", null, "Robot Registration MVP"),
          e("p", null, "Base Station | Dashboard")
        ),
        e(
          "div",
          { className: "status" },
          e("span", {
            className: connected ? "status-dot connected" : "status-dot",
          }),
          e("span", { className: "status-label" }, connected ? "Connected" : "Disconnected")
        )
      ),
      e(
        "section",
        { className: "stage" },
        e(
          "div",
          { className: "tabs" },
          e("div", { className: "tabs-label" }, "Views"),
          renderTabButton("vision", "Vision Feed"),
          renderTabButton("slam", "SLAM Map"),
          renderTabButton("face", "Face Recognition"),
          renderTabButton("telemetry", "Telemetry")
        ),
        stageContent
      ),
      e(
        "aside",
        { className: "panel" },
        e(
          "div",
          { className: "panel-card" },
          e("h2", null, "Control Panel"),
          e("p", { className: "panel-muted" }, "Drive control via virtual joystick."),
          e(
            "div",
            { className: "joystick-shell" },
            e("div", { className: autonomyActive ? "joystick-zone locked" : "joystick-zone", ref: joystickRef }),
            autonomyActive ? e("div", { className: "joystick-lock-hint" }, "Locked — autonomy running") : null
          ),
          autonomyControls,
          e(
            "div",
            { className: "panel-footer" },
            autonomyActive
              ? autonomyState === "mapping"
                ? "Autonomous mapping active."
                : "Face seeking active."
              : joystickStatus === "ready"
              ? "Release the stick to stop."
              : joystickStatus === "loading"
              ? "Loading joystick..."
              : "Joystick library unavailable."
          )
        )
      )
    );
  }

  const root = ReactDOM.createRoot(document.getElementById("app"));
  root.render(e(App));
})();
