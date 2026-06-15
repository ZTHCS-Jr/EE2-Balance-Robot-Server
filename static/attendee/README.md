# EE2-Balance-Robot-Attendee-UI
Simple attendee view of web UI. Concept is that it is to allow attendees to network with their peers using the environment map and for them to be able to register at the event if the robot has identified them as unregistered.

Registration process is connected to AWS, and shares the database with the Controller UI

Setup:
```bash
# install
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update
sudo apt install ngrok

# sign up + authenticate
```

Run:
```bash
nrgok http 8080

# follow the forwarded link
```