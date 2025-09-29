#!/usr/bin/env python3
import threading
import time
from queue import Queue
from collections import deque
from flask import Flask, request, jsonify, request as flask_request
import requests

# ==== PJSIP (pjsua) ====
import pjsua as pj

# ======================= Konfigurasi =======================
PORT = 7000
RING_TIMEOUT_SEC = 45
RETRY_GAP_SEC = 4
CLIENT_PORT_DEFAULT = 6000

SIP_DOMAIN = "ld.infin8link.com"
SIP_HOSTPORT = "ld.infin8link.com:7060"
SIP_REG_URI = f"sip:{SIP_HOSTPORT}"
SIP_DIAL_SUFFIX = f"@{SIP_HOSTPORT}"   # sip:<number>@ld.infin8link.com:7060
# ===========================================================

app = Flask(__name__)

# ======= State global =======
connected_clients = set()    # contoh: "http://192.168.88.201:6000"
call_queue = Queue()
state_lock = threading.Lock()

call_status = {
    "running": False,
    "paused": False,
    "stopped": False,
    "in_progress": None,   # dict info item berjalan
    "processed": 0,
    "queued": 0,
    "active_sip_user": None
}

pause_event = threading.Event()  # set() -> jalan; clear() -> pause
stop_event  = threading.Event()  # set() -> stop

# ======= Event Bus (untuk realtime polling dari Windows) =======
EVENT_MAX = 2000
events_buf = deque(maxlen=EVENT_MAX)  # simpan dict event
event_lock = threading.Lock()
event_seq = 0

def publish_event(ev: dict, also_broadcast=True):
    """Simpan event ke buffer + optional broadcast ke Windows."""
    global event_seq
    with event_lock:
        event_seq += 1
        ev_out = dict(ev)
        ev_out["event_id"] = event_seq
        ev_out["ts"] = time.time()
        events_buf.append(ev_out)
    if also_broadcast:
        try:
            broadcast_to_clients("/receive-info", ev.get("payload", ev))
        except Exception:
            pass
    return ev_out

# ===========================================================
#                 PJSIP: Library & Account
# ===========================================================
def _log_cb(level, s, length):
    try:
        print(s.strip())
    except Exception:
        pass

class _CallCb(pj.CallCallback):
    """Callback panggilan outgoing; PJSIP akan mengisi self.call."""
    def __init__(self, answered_event, disconnected_event):
        super().__init__()
        self.answered_event = answered_event
        self.disconnected_event = disconnected_event
        self.confirmed = False
        self.last_reason = ""

    def on_state(self):
        ci = self.call.info()
        print(f"[PJSIP] Call state: {ci.state_text} | code={ci.last_code} reason={ci.last_reason}")
        if ci.state == pj.CallState.CONFIRMED and not self.confirmed:
            self.confirmed = True
            if not self.answered_event.is_set():
                self.answered_event.set()
        if ci.state == pj.CallState.DISCONNECTED:
            self.last_reason = ci.last_reason or ""
            if not self.disconnected_event.is_set():
                self.disconnected_event.set()

class SipManager:
    def __init__(self):
        self.lib = None
        self.acc = None
        self.acc_user = None
        self.acc_pass = None
        self.lock = threading.Lock()
        self._init_lib()

    def _init_lib(self):
        self.lib = pj.Lib()
        self.lib.init(log_cfg=pj.LogConfig(level=2, callback=_log_cb))
        # transport UDP & TCP
        self.lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(0))
        self.lib.create_transport(pj.TransportType.TCP, pj.TransportConfig(0))
        self.lib.start()
        # Nonaktifkan audio device (server headless)
        self.lib.set_null_snd_dev()
        print("[PJSIP] Library started (null audio).")

    def _destroy_acc(self):
        if self.acc:
            try:
                self.acc.delete()
            except Exception:
                pass
            self.acc = None
            self.acc_user = None
            self.acc_pass = None

    def ensure_account(self, username: str, password: str):
        with self.lock:
            if self.acc and self.acc_user == username and self.acc_pass == password:
                return
            self._destroy_acc()
            cfg = pj.AccountConfig()
            cfg.id = f"sip:{username}@{SIP_DOMAIN}"
            cfg.reg_uri = SIP_REG_URI
            cfg.auth_cred = [pj.AuthCred("*", username, password)]
            # Paksa UDP? uncomment:
            # cfg.proxy = [f"sip:{SIP_HOSTPORT};transport=udp"]
            self.acc = self.lib.create_account(cfg)
            self.acc_user = username
            self.acc_pass = password
            # Tunggu register (maks 5 detik)
            t0 = time.time()
            while time.time() - t0 < 5:
                if self.acc.info().reg_status == 200:
                    print(f"[PJSIP] Registered as {username}")
                    return
                time.sleep(0.2)
            print(f"[PJSIP] Register pending/failed: {self.acc.info().reg_status}")

    def dial_then_transfer_to_agent(self, target_number: str, ring_timeout_sec: int, agent_user: str | None):
        """
        1) Dial ke target_number
        2) Jika terjawab dan agent_user diberikan -> REFER (transfer) ke agent_user@host:port
        3) Hangup leg dialer (media berjalan antara agent<->nasabah via SIP server)
        """
        if not self.acc:
            return {"answered": False, "detail": "no_account"}

        uri = f"sip:{target_number}{SIP_DIAL_SUFFIX}"
        answered_evt = threading.Event()
        disconnected_evt = threading.Event()

        cb = _CallCb(answered_evt, disconnected_evt)
        call = self.acc.make_call(uri, cb)

        # Tunggu answered atau timeout/disconnect
        t0 = time.time()
        answered = False
        while time.time() - t0 < ring_timeout_sec:
            if answered_evt.is_set():
                answered = True
                break
            if disconnected_evt.is_set():
                break
            time.sleep(0.2)

        detail = "timeout"
        if answered and agent_user:
            try:
                refer_to = f"sip:{agent_user}@{SIP_HOSTPORT}"
                call.xfer(refer_to)  # unattended transfer (SIP REFER)
                detail = f"transferred_to:{agent_user}"
            except Exception as e:
                detail = f"transfer_error:{e}"
        elif answered and not agent_user:
            detail = "answered"
        else:
            if disconnected_evt.is_set():
                detail = cb.last_reason or "disconnected"

        # Tutup leg dialer
        try:
            call.hangup()
        except Exception:
            pass

        return {"answered": answered, "detail": detail}

    def destroy(self):
        self._destroy_acc()
        if self.lib:
            try:
                self.lib.destroy()
            except Exception:
                pass
            self.lib = None

sip = SipManager()

# ===========================================================
#            Broadcast util + helper format event
# ===========================================================
def broadcast_to_clients(path, payload):
    dead = []
    for base in list(connected_clients):
        url = f"{base}{path}"
        try:
            requests.post(url, json=payload, timeout=2.5)
        except Exception:
            dead.append(base)
    for d in dead:
        connected_clients.discard(d)

def make_progress_payload(item, phase, number, answered, detail):
    return {
        "user": {"id_system": "-", "username": "worker", "phone": "-"},
        "data": [item],
        "progress": {
            "phase": phase,
            "number": number,
            "answered": answered,
            "detail": detail
        }
    }

# ===========================================================
#                     Worker antrian
# ===========================================================
def call_flow_worker():
    while True:
        item = call_queue.get()
        if item is None:
            break

        username = item.get("_sip_user")  # agent SIP (MicroSIP di Windows)
        password = item.get("_sip_pass")

        with state_lock:
            call_status["in_progress"] = {
                "nama_nasabah": item.get("nama_nasabah"),
                "phone": item.get("phone"),
                "ec_name_1": item.get("ec_name_1"),
                "ec_phone_1": item.get("ec_phone_1"),
                "ec_name_2": item.get("ec_name_2"),
                "ec_phone_2": item.get("ec_phone_2"),
                "total_tagihan": item.get("total_tagihan"),
            }
            call_status["active_sip_user"] = username

        # Pause/Stop handling
        while not pause_event.is_set():
            if stop_event.is_set():
                break
            time.sleep(0.2)
        if stop_event.is_set():
            call_queue.task_done()
            with state_lock:
                call_status["in_progress"] = None
            continue

        # Login SIP
        try:
            sip.ensure_account(username, password)
        except Exception as e:
            payload = make_progress_payload(item, "LOGIN", "-", False, f"login_failed:{e}")
            publish_event({"type": "progress", "payload": payload})
            with state_lock:
                call_status["processed"] += 1
                call_status["in_progress"] = None
            call_queue.task_done()
            continue

        numbers = [
            ("NASABAH", item.get("phone")),
            ("EC1", item.get("ec_phone_1")),
            ("EC2", item.get("ec_phone_2")),
        ]

        for label, number in numbers:
            if not number:
                continue

            if stop_event.is_set():
                break
            while not pause_event.is_set():
                if stop_event.is_set():
                    break
                time.sleep(0.2)
            if stop_event.is_set():
                break

            # INFO: sedang menelepon nomor apa (event + broadcast)
            payload_calling = make_progress_payload(item, f"CALLING {label}", number, None, "ringing")
            publish_event({"type": "progress", "payload": payload_calling})

            # NASABAH -> transfer ke agent; EC1/EC2 -> tanpa transfer (bisa diubah)
            agent = username if label == "NASABAH" else None
            try:
                result = sip.dial_then_transfer_to_agent(number, RING_TIMEOUT_SEC, agent_user=agent)
            except Exception as e:
                result = {"answered": False, "detail": f"error:{e}"}

            print(f"[DIAL] {label} {number} -> answered={result['answered']} ({result['detail']})")

            payload_done = make_progress_payload(item, label, number, result["answered"], result["detail"])
            publish_event({"type": "progress", "payload": payload_done})

            # Selesai jika:
            if label == "NASABAH" and result["answered"]:
                break       # ditransfer ke agent (Windows)
            if label in ("EC1", "EC2") and result["answered"]:
                break       # (opsional) berhenti jika EC menjawab

            time.sleep(RETRY_GAP_SEC)

        with state_lock:
            call_status["processed"] += 1
            call_status["in_progress"] = None
        call_queue.task_done()

# Jalankan worker
pause_event.set()
stop_event.clear()
worker_thread = threading.Thread(target=call_flow_worker, daemon=True)
worker_thread.start()

# ===========================================================
#                    API endpoints
# ===========================================================
@app.route("/register-client", methods=["POST"])
def register_client():
    data = flask_request.json or {}
    ip = data.get("ip")
    port = data.get("port", CLIENT_PORT_DEFAULT)
    if not ip:
        return jsonify({"status": "error", "message": "ip diperlukan"}), 400
    base = f"http://{ip}:{port}"
    connected_clients.add(base)
    print(f"✅ Client terdaftar: {base}")
    return jsonify({"status": "ok", "connected_clients": list(connected_clients)}), 200

# body: { "user": {"num_sip":"", "pas_sip":""}, "data":[{...}, ...] }
@app.route("/push-data", methods=["POST"])
def push_data():
    payload = flask_request.json or {}
    dataset = payload.get("data", [])
    u = payload.get("user", {}) or {}
    sip_user = u.get("num_sip")
    sip_pass = u.get("pas_sip")

    if not isinstance(dataset, list) or not dataset:
        return jsonify({"status": "error", "message": "data kosong/invalid"}), 400
    if not sip_user or not sip_pass:
        return jsonify({"status": "error", "message": "num_sip/pas_sip kosong"}), 400

    # broadcast tabel awal (opsional)
    publish_event({"type": "dataset", "payload": payload})  # juga broadcast

    added = 0
    for row in dataset:
        row = dict(row)
        row["_sip_user"] = sip_user
        row["_sip_pass"] = sip_pass
        call_queue.put(row)
        added += 1

    with state_lock:
        call_status["queued"] += added

    return jsonify({"status": "ok", "enqueued": added, "queue_size": call_queue.qsize()}), 200

@app.route("/api/<action>", methods=["POST"])
def handle_action(action):
    code = 200
    if action == "call":
        with state_lock:
            call_status["running"] = True
            call_status["paused"] = False
            call_status["stopped"] = False
        pause_event.set()
        stop_event.clear()
        msg = "Call dimulai (worker aktif)"

    elif action == "pause":
        with state_lock:
            if call_status["running"]:
                call_status["paused"] = True
                pause_event.clear()
                msg = "Call dipause"
            else:
                msg = "Call belum berjalan"

    elif action == "start":
        with state_lock:
            if call_status["running"] and call_status["paused"]:
                call_status["paused"] = False
                pause_event.set()
                msg = "Call dilanjutkan"
            else:
                msg = "Call belum dipause"

    elif action == "stop":
        with state_lock:
            if call_status["running"]:
                call_status["running"] = False
                call_status["paused"] = False
                call_status["stopped"] = True
            else:
                call_status["stopped"] = True
        stop_event.set()
        pause_event.set()

        drained = 0
        try:
            while True:
                call_queue.get_nowait()
                call_queue.task_done()
                drained += 1
        except Exception:
            pass
        msg = f"Call dihentikan (queue dikosongkan {drained} item)"

    else:
        msg = "Action tidak dikenal"
        code = 400

    print(f"[ACTION] {action.upper()} -> {msg}")
    publish_event({"type": "action", "payload": {"action": action, "message": msg}}, also_broadcast=False)
    return jsonify({"status": "ok" if code == 200 else "error", "action": action, "message": msg}), code

@app.route("/api/log", methods=["GET"])
def get_status():
    with state_lock:
        s = dict(call_status)
        s["queue_size"] = call_queue.qsize()
    return jsonify(s), 200

@app.route("/events", methods=["GET"])
def get_events():
    """
    Polling incremental:
      GET /events?since=<event_id_terakhir_yang_sudah_diproses>
    Return: { "events":[...], "last_id": <id_terakhir> }
    """
    try:
        since = int(flask_request.args.get("since", "0"))
    except Exception:
        since = 0
    with event_lock:
        out = [e for e in events_buf if e["event_id"] > since]
        last_id = events_buf[-1]["event_id"] if events_buf else since
    return jsonify({"events": out, "last_id": last_id})

# ======= Main =======
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
    finally:
        try:
            sip.destroy()
        except Exception:
            pass
