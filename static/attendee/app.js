(() => {
  const e = React.createElement;

  const ANGLES = [
    { key: "front", label: "Front", prompt: "Look straight at the camera" },
    { key: "left", label: "Left", prompt: "Turn your head slightly left" },
    { key: "right", label: "Right", prompt: "Turn your head slightly right" },
  ];

  function AttendeeApp() {
    const [activeTab, setActiveTab] = React.useState("map"); 
    const [mapSrc, setMapSrc] = React.useState(null);
    const [name, setName] = React.useState("");
    const [language, setLanguage]=React.useState("English");
    const [status, setStatus] = React.useState("idle"); 
    const [errorMessage, setErrorMessage] = React.useState("");
    const [step, setStep] = React.useState(0);

    const videoRef = React.useRef(null);
    const canvasRef = React.useRef(null);
    const streamRef = React.useRef(null);

    React.useEffect(() => {
      const wsUrl = `wss://${window.location.host}/ws/ui`;
      const ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          try {
            const payload = JSON.parse(event.data);
            if (payload.type === "map") {
              setMapSrc(payload.image);
            }
          } catch (err) {
            // ignore JSON parse errors
          }
        }
      };

      return () => ws.close(); // properly closes the connection
    }, []);

    React.useEffect(() => {
      if (activeTab !== "register") {
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
        return;
      }

      let cancelled = false;
      navigator.mediaDevices
        .getUserMedia({
          video: { facingMode: "user", width: { ideal: 640 } },
          audio: false,
        })
        .then((stream) => {
          if (cancelled) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          streamRef.current = stream;
          if (videoRef.current) videoRef.current.srcObject = stream;
        })
        .catch((err) => {
          if (!cancelled) {
            setStatus("error");
            setErrorMessage("Camera access is required to register.");
          }
        });

      return () => {
        cancelled = true;
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
        }
      };
    }, [activeTab]);

    const handleCapture = async () => {
      if (!name.trim()) {
        setStatus("error");
        setErrorMessage("Please enter your full name.");
        return;
      }

      const video = videoRef.current;
      const canvas = canvasRef.current;

      if (!video || video.videoWidth === 0) return;

      setStatus("loading");
      setErrorMessage("");

      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const base64Image = canvas.toDataURL("image/jpeg", 0.92);
      
      const currentAngle = ANGLES[step];
      const uniqueName = `${name.trim()}-${currentAngle.key}`;

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
            name: uniqueName,
            image: base64Image,
            language: language
          })
        });

        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.message || "Server rejected image");
        }

        if (step < ANGLES.length - 1) {
          setStep((prev) => prev + 1);
          setStatus("idle"); 
        } else {
          if (streamRef.current) streamRef.current.getTracks().forEach((t) => t.stop());
          setStatus("success");
        }

      } catch (err) {
        setStatus("error");
        setErrorMessage(err.message || "Network error connecting to AWS");
      }
    };

    const renderTabs = () => e(
      "div", { className: "mobile-tabs" },
      e("div", { 
        className: `mobile-tab ${activeTab === "map" ? "active" : ""}`,
        onClick: () => setActiveTab("map")
      }, "Live Map"),
      e("div", { 
        className: `mobile-tab ${activeTab === "register" ? "active" : ""}`,
        onClick: () => setActiveTab("register")
      }, "Register")
    );

    if (activeTab === "map") {
      return e("div", { className: "auth-card" },
        renderTabs(),
        e("div", { className: "auth-header" },
          e("h1", null, "Event Map"),
          e("p", null, "Robot's map with other attendee's last-seen location")
        ),
        e("div", { className: "map-wrapper", style: { textAlign: "center", marginTop: "30px" } },
          mapSrc 
            ? e("img", { 
                src: mapSrc, 
                alt: "Live SLAM Map",
                style: { 
                  width: "100%", 
                  maxWidth: "400px", 
                  borderRadius: "8px", 
                  border: "2px solid #ccc",
                  boxShadow: "0 4px 6px rgba(0,0,0,0.1)"
                } 
              })
            : e("div", { 
                style: { 
                  padding: "40px 20px", 
                  background: "#f8f9fa", 
                  borderRadius: "8px", 
                  color: "#6c757d" 
                } 
              }, 
              "Waiting for map telemetry from robot..."
            )
        )
      );
    }

    if (status === "success") {
      return e("div", { className: "auth-card success-stage" },
        e("div", { className: "success-icon" }, "🎉"),
        e("div", { className: "auth-header" },
          e("h1", null, "Registration Complete!"),
          e("p", null, `Thanks for registering, ${name.trim()}. The robot will now recognise you at the event`)
        ),
        e("button", { 
          className: "btn-primary", 
          onClick: () => {
            setStep(0);
            setName("");
            setStatus("idle");
            setActiveTab("map");
          } 
        }, "Return to Map")
      );
    }

    const currentAngle = ANGLES[step];
    return e("div", { className: "auth-card" },
      renderTabs(),
      e("div", { className: "auth-header" },
        e("h1", null, "Robot Registration"),
        e("p", null, "3 quick photos to help the robot recognize you")
      ),
      
      e("div", { className: "input-group" },
        e("label", { htmlFor: "nameInput" }, "Full Name"),
        e("input", {
          id: "nameInput",
          type: "text",
          value: name,
          onChange: (ev) => setName(ev.target.value),
          placeholder: "e.g. Alice Smith",
          disabled: status === "loading" || step > 0,
        })
      ),

      e("div", { className: "input-group" },
        e("label", { htmlFor: "langInput" }, "Preferred Greeting Language"),
        e("select", {
          id: "langInput",
          value: language,
          onChange: (ev) => setLanguage(ev.target.value),
          disabled: status === "loading" || step > 0,
        }, 
          e("option", { value: "English" }, "English"),
          e("option", { value: "Greek" }, "Greek"),
          e("option", { value: "French" }, "French"),
          e("option", { value: "Mandarin" }, "Mandarin"),
          e("option", { value: "Japanese" }, "Japanese")
        )
      ),

      e("div", { className: "video-wrapper" },
        e("video", { ref: videoRef, autoPlay: true, playsInline: true, muted: true, className: "selfie-video" }),
        e("canvas", { ref: canvasRef, style: { display: "none" } }),
        e("div", { style: { position: "absolute", top: "10px", right: "10px", background: "rgba(0,0,0,0.6)", color: "white", padding: "4px 8px", borderRadius: "8px", fontSize: "12px", fontWeight: "bold" } }, `Photo: ${step + 1}/3`)
      ),
      
      e("p", { style: { fontWeight: "600", color: "var(--accent)", margin: "0 0 16px 0" } }, currentAngle.prompt),

      e("button", {
        className: "btn-primary",
        onClick: handleCapture,
        disabled: status === "loading" || !name.trim(),
      }, status === "loading" ? "Processing..." : `Capture ${currentAngle.label} Angle`),
      
      status === "error" && errorMessage ? e("div", { className: "status-msg error" }, errorMessage) : null
    );
  }

  const root = ReactDOM.createRoot(document.getElementById("app"));
  root.render(e(AttendeeApp));
})();