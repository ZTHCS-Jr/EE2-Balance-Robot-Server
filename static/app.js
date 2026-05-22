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

  function App() {
    const [connected, setConnected] = React.useState(false);
    const [joystickStatus, setJoystickStatus] = React.useState("loading");
    const [activeTab, setActiveTab] = React.useState("slam");
    const [hasVideoStream, setHasVideoStream] = React.useState(false);
    const [telemetry, setTelemetry] = React.useState({
      type: "telemetry",
      timestamp: Date.now(),
      battery_capacity: 85.5,
      power_consumption: 12.4,
      imu_angle: 0.2,
      last_linear: 0,
      last_angular: 0,
    });
    
    const joystickRef = React.useRef(null);
    const wsRef = React.useRef(null);
    const videoImgRef = React.useRef(null);
    const latestRef = React.useRef({ x: 0, y: 0 });
    const sendTimerRef = React.useRef(null);
    const reconnectRef = React.useRef({ timer: null, attempts: 0 });

    const sendPayload = React.useCallback((payload) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
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
          console.log("INCOMING DATA TYPE:", typeof event.data, event.data);
          if (!event.data) return;

          if (event.data instanceof Blob) {
            if (videoImgRef.current) {
              setHasVideoStream(true);
              const url = URL.createObjectURL(event.data);
              const oldUrl = videoImgRef.current.src;
              
              videoImgRef.current.src = url;
              
              // Clear old tracking cache
              if (oldUrl && oldUrl.startsWith("blob:")) {
                URL.revokeObjectURL(oldUrl);
              }
            }
            return;
          }

          try {
            const payload = JSON.parse(event.data);
            if (payload && payload.type === "telemetry") {
              setTelemetry(normalizeTelemetry(payload));
            }
          } catch (error) {
            return;
          }
        });

        ws.addEventListener("close", () => {
          setConnected(false);
          setHasVideoStream(false);
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
        const { x, y } = latestRef.current;
        sendPayload({ type: "joystick", x, y });
      };

      const startSending = () => {
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
        const angular = clamp(data.vector.x * force, -1, 1);
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

    return e(
      "div",
      { className: "app" },
      e(
        "svg",
        { 
          width:0, height:0, 
          style: {position: "absolute", visibility: "hidden"},
        },
        e(
          "filter",
          {id:"noir-matrix"},
          e(
            "feColorMatrix",
            {
              type: "matrix",
              values:"0.02 0 0 0 0   0 0.8 0 0 0   0 0 0.3 0 0   0 0 0 1 0"
            }
          )
        )
      ),
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
          renderTabButton("slam", "SLAM + Vision"),
          renderTabButton("telemetry", "Telemetry")
        ),
        activeTab === "slam"
          ? e(
              "div",
              { className: "stage-placeholder", style: { position: "relative", overflow: "hidden" } },
              e("img", {
                ref: videoImgRef,
                style: {
                  display: hasVideoStream ? "block" : "none",
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  borderRadius: "12px",
                filter: "url(#noir-matrix)" 
                },
                alt: "Pi Camera Feed"
              }),
              e(
                "div",
                { style: { display: hasVideoStream ? "none" : "block", textAlign: "center" } },
                e("div", { className: "stage-title" }, "Pi NoIR Camera Feed Offline"),
                e("div", { className: "stage-subtitle" }, "Awaiting video processing frame tokens from Pi 3...")
              )
            )
          : e(
              "div",
              { className: "telemetry" },
              e(
                "div",
                { className: "telemetry-grid" },
                e(
                  "div",
                  { className: "gauge-card" },
                  e("div", { className: "gauge-label" }, "Battery"),
                  e(
                    "div",
                    { className: "gauge", style: { "--value": Math.max(0, Math.min(100, telemetry.battery_capacity)) } },
                    e("div", { className: "gauge-value" }, `${telemetry.battery_capacity.toFixed(1)}%`)
                  )
                ),
                e(
                  "div",
                  { className: "gauge-card" },
                  e("div", { className: "gauge-label" }, "Power"),
                  e(
                    "div",
                    {
                      className: "gauge",
                      style: {
                        "--value": Math.max(0, Math.min(100, telemetry.power_consumption * 5)),
                      },
                    },
                    e("div", { className: "gauge-value" }, `${telemetry.power_consumption.toFixed(1)} W`)
                  )
                ),
                e(
                  "div",
                  { className: "gauge-card" },
                  e("div", { className: "gauge-label" }, "IMU Angle"),
                  e(
                    "div",
                    {
                      className: "gauge",
                      style: {
                        "--value": Math.max(0, Math.min(100, (telemetry.imu_angle + 1) * 50)),
                      },
                    },
                    e("div", { className: "gauge-value" }, `${telemetry.imu_angle.toFixed(2)} rad`)
                  )
                ),
                e(
                  "div",
                  { className: "gauge-card" },
                  e("div", { className: "gauge-label" }, "Velocity"),
                  e(
                    "div",
                    { className: "gauge", style: { "--value": Math.max(0, Math.min(100, Math.abs(telemetry.last_linear) * 100)) } },
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
            )
      ),
      e(
        "aside",
        { className: "panel" },
        e(
          "div",
          { className: "panel-card" },
          e("h2", null, "Control Panel"),
          e("p", { className: "panel-muted" }, "Drive control via virtual joystick."),
          e("div", { className: "joystick-shell" }, e("div", { className: "joystick-zone", ref: joystickRef })),
          e(
            "div",
            { className: "panel-footer" },
            joystickStatus === "ready"
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