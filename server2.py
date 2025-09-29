import threading
from flask import Flask, request, jsonify
import time

app = Flask(__name__)

# Simpan list IP client yang terdaftar
connected_clients = []

# Simulasi status call
call_status = {
    "running": False,
    "paused": False
}

# =======================
# API untuk terima log client (opsional)
# =======================
@app.route("/register-client", methods=["POST"])
def register_client():
    data = request.json
    client_ip = data.get("ip")
    if client_ip and client_ip not in connected_clients:
        connected_clients.append(client_ip)
        print(f"✅ Client baru terdaftar: {client_ip}")
    return jsonify({"status": "ok", "connected_clients": connected_clients})

# =======================
# API action: call, start, pause, stop
# =======================
@app.route("/api/<action>", methods=["POST"])
def handle_action(action):
    global call_status
    if action == "call":
        if not call_status["running"]:
            call_status["running"] = True
            call_status["paused"] = False
            msg = "Call dimulai"
        else:
            msg = "Call sudah berjalan"
    elif action == "pause":
        if call_status["running"]:
            call_status["paused"] = True
            msg = "Call dipause"
        else:
            msg = "Call belum berjalan"
    elif action == "start":
        if call_status["running"] and call_status["paused"]:
            call_status["paused"] = False
            msg = "Call dilanjutkan"
        else:
            msg = "Call belum dipause"
    elif action == "stop":
        if call_status["running"]:
            call_status["running"] = False
            call_status["paused"] = False
            msg = "Call dihentikan"
        else:
            msg = "Call belum berjalan"
    else:
        msg = "Action tidak dikenal"

    print(f"[ACTION] {action.upper()} -> {msg}")
    return jsonify({"status": "ok", "action": action, "message": msg})

# =======================
# API untuk test call / monitoring log
# =======================
@app.route("/api/log", methods=["GET"])
def get_status():
    status_msg = "Running" if call_status["running"] else "Stopped"
    paused_msg = "Paused" if call_status["paused"] else "Active"
    return jsonify({"running": call_status["running"], "paused": call_status["paused"], "status": f"{status_msg} ({paused_msg})"})

# =======================
# Main
# =======================
if __name__ == "__main__":
    # Jalankan Flask server di semua IP, port 7000
    app.run(host="0.0.0.0", port=7000, debug=False, threaded=True)
