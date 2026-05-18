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

  function App() {
    const [connected, setConnected] = React.useState(false);
    const [joystickStatus, setJoystickStatus] = React.useState("loading");
    const joystickRef = React.useRef(null);
    const wsRef = React.useRef(null);
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
        if (cancelled) {
          return;
        }
        setJoystickStatus(ready ? "ready" : "missing");
        if (!ready) {
          console.warn("Nipple.js failed to load. Offline users can add /static/vendor/nipplejs.min.js.");
        }
      });

      return () => {
        cancelled = true;
      };
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
        wsRef.current = ws;

        ws.addEventListener("open", () => {
          setConnected(true);
          reconnectRef.current.attempts = 0;
        });

        ws.addEventListener("close", () => {
          setConnected(false);
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
        return undefined;
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
        if (!data || !data.vector) {
          return;
        }
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

    // Phase 3 hook: replace the stage placeholder with SLAM and vision widgets.
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
          { className: "stage-placeholder" },
          e("div", { className: "stage-title" }, "SLAM/Vision Feed Offline"),
          e(
            "div",
            { className: "stage-subtitle" },
            "Will replace this panel with map tiles, camera streams, or 3D views later."
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
          e(
            "div",
            { className: "joystick-shell" },
            e("div", { className: "joystick-zone", ref: joystickRef })
          ),
          e(
            "div",
            { className: "panel-footer" },
            joystickStatus === "ready"
              ? "Release the stick to stop."
              : joystickStatus === "loading"
              ? "Loading joystick..."
              : "Joystick library unavailable. If offline, add /static/vendor/nipplejs.min.js."
          )
        )
      )
    );
  }

  const root = ReactDOM.createRoot(document.getElementById("app"));
  root.render(e(App));
})();
