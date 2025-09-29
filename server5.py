import threading
import time
from queue import Queue
from flask import Flask, request, jsonify
import requests

# ==== PJSIP (pjsua) ====
# Pastikan sudah terinstal pjsip binding Python: import pjsua as pj
import pjsua as pj

# ======================= Konfigurasi =======================
PORT = 7000
RING_TIMEOUT_SEC = 45
RETRY_GAP_SEC = 4
CLIENT_PORT_DEFAULT = 6000

SIP_DOMAIN = "ld.infin8link.com"
SIP_REG_URI = "sip:ld.infin8link.com:6070"
SIP_DIAL_SUFFIX = "@ld.infin8link.com:6070"    # tujuan dial
USE_UDP = True
USE_TCP = True
LOCAL_UDP_PORT = 0  # 0 = auto
LOCAL_TCP_PORT = 0  # 0 = auto
# ===========================================================

app = Flask(__name__)

# ======= State global =======
connected_clients = set()    # misal: http://192.168.88.123:6000
call_queue = Queue()
state_lock = threading.Lock()

call_status = {
    "running": False,
    "paused": False,
    "stopped": False,
    "in_progress": None,
    "processed": 0,
    "queued": 0,
    "active_sip_user": None
}

pause_event = threading.Event()   # set() -> jalan; clear() -> pause
stop_event = threading.Event()    # set() -> stop

# ===========================================================
#                 PJSIP: Library & Account
# ===========================================================
def log_cb(level, s, length):
    try:
        print(s.strip())
    except Exception:
        pass

class _CallCb(pj.CallCallback):
    def __init__(self, call, done_event):
        super().__init__(call)
        self.done_event = done_event
        self.answered = False
        self.result_detail = ""

    def on_state(self):
        ci = self.call.info()
        print(f"[PJSIP] Call state: {ci.state_text}, code={ci.last_code} ({ci.last_reason})")
        # CONFIRMED = answered, DISCONNECTED = selesai
        if ci.state == pj.CallState.CONFIRMED:
            self.answered = True
            self.result_detail = "answered"
        elif ci.state == pj.CallState.DISCONNECTED:
            if not self.answered:
                self.result_detail = ci.last_reason or "disconnected"
            # signal selesai tunggu
            if not self.done_event.is_set():
                self.done_event.set()

class SipManager:
    """
    Manage pjsip Lib, transports, and a single active Account (register).
    Ganti akun saat batch berbeda kredensial.
    """
    def __init__(self):
        self.lib = None
        self.acc = None
        self.acc_user = None
        self.acc_pass = None
        self.lock = threading.Lock()
        self._init_lib()

    def _init_lib(self):
        self.lib = pj.Lib()
        self.lib.init(log_cfg=pj.LogConfig(level=3, callback=log_cb))
        # transport
        if USE_UDP:
            self.lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(LOCAL_UDP_PORT))
        if USE_TCP:
            self.lib.create_transport(pj.TransportType.TCP, pj.TransportConfig(LOCAL_TCP_PORT))
        self.lib.start()
        print("[PJSIP] Library started.")

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
        """
        Pastikan account yang login sama dengan username/password yang diminta.
        Kalau berbeda / belum login, re-create account dan register.
        """
        with self.lock:
            if self.acc and self.acc_user == username and self.acc_pass == password:
                return  # sudah sesuai

            # recreate account
            self._destroy_acc()

            acc_cfg = pj.AccountConfig()
            acc_cfg.id = f"sip:{username}@{SIP_DOMAIN}"
            acc_cfg.reg_uri = SIP_REG_URI
            # Bisa tambahkan proxy kalau mau paksa transport tertentu:
            # acc_cfg.proxy = [f"sip:{SIP_DOMAIN}:6070;transport=udp"]
            acc_cfg.auth_cred = [pj.AuthCred(realm="*", username=username, data=password)]
            self.acc = self.lib.create_account(acc_cfg)
            self.acc_user = username
            self.acc_pass = password

            # Tunggu state registered (simple wait)
            t0 = time.time()
            while time.time() - t0 < 5:
                ai = self.acc.info()
                if ai.reg_status == 200:
                    print(f"[PJSIP] Registered as {username} ({ai.uri})")
                    return
                time.sleep(0.2)
            print(f"[PJSIP] Warning: register pending/failed (status={self.acc.info().reg_status})")

    def dial_once(self, number: str, ring_timeout_sec: int) -> dict:
        """
        Lakukan 1 panggilan ke `number` dan tunggu hasil sampai:
        - answered (CONFIRMED),
        - atau DISCONNECTED dengan alasan lain,
        - atau time out -> hangup.
        """
        if not self.acc:
            return {"answered": False, "duration": 0, "detail": "no_account"}

        uri = f"sip:{number}{SIP_DIAL_SUFFIX}"
        done = threading.Event()
        call = self.acc.make_call(uri, _CallCb(self.acc._current_call, done)) if hasattr(self.acc, "_current_call") \
            else self.acc.make_call(uri, _CallCb(None, done))

        # Tunggu sampai answered/disconnected atau timeout
        t0 = time.time()
        answered = False
        detail = "timeout"

        while time.time() - t0 < ring_timeout_sec:
            if done.is_set():
                # call callback sudah set finished
                cb = call.callback
                answered = getattr(cb, "answered", False)
                detail = getattr(cb, "result_detail", "done")
                break
            time.sleep(0.2)

        # Jika timeout dan belum selesai, hangup
        if not done.is_set():
            try:
                call.hangup()
            except Exception:
                pass
            # beri sedikit waktu untuk state update
            time.sleep(0.5)

        # Tutup call jika masih ada
        try:
            call.hangup()
        except Exception:
            pass

        return {"answered": answered, "duration": 0, "detail": detail}

    def destroy(self):
        with self.lock:
            self._destroy_acc()
            if self.lib:
                try:
                    self.lib.destroy()
                except Exception:
                    pass
                self.lib = None

sip = SipManager()

# ===========================================================
#                 Broadcast util ke Windows
# ===========================================================
def broadcast_to_clients(path, payload):
    dead = []
    for base in list(connected_clients):
        url = f"{base}{path}"
        try:
            requests.post(url, json=payload, timeout=3)
        except Exception:
            dead.append(base)
    for d in dead:
        connected_clients.discard(d)

# ===========================================================
#                     Worker antrian
# ===========================================================
def call_flow_worker():
    while True:
        item = call_queue.get()
        if item is None:
            break

        # item sudah membawa kredensial SIP
        username = item.get("_sip_user")
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

        # Tunggu kalau pause
        while not pause_event.is_set():
            if stop_event.is_set():
                break
            time.sleep(0.2)
        if stop_event.is_set():
            call_queue.task_done()
            with state_lock:
                call_status["in_progress"] = None
            continue

        # Pastikan login SIP sesuai akun batch
        try:
            sip.ensure_account(username, password)
        except Exception as e:
            print(f"[PJSIP] ensure_account error: {e}")
            # Broadcast error sekali
            try:
                broadcast_to_clients("/receive-info", {
                    "user": {"id_system": "-", "username": "worker", "phone": "-"},
                    "data": [item],
                    "progress": {"phase": "LOGIN", "number": "-", "answered": False, "detail": f"login_failed: {e}"}
                })
            except Exception:
                pass
            # skip seluruh item ini
            with state_lock:
                call_status["processed"] += 1
                call_status["in_progress"] = None
            call_queue.task_done()
            continue

        # Urutan nomor
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

            # Broadcast "sedang memanggil"
            try:
                broadcast_to_clients("/receive-info", {
                    "user": {"id_system": "-", "username": "worker", "phone": "-"},
                    "data": [item],
                    "progress": {"phase": f"CALLING {label}", "number": number, "answered": None, "detail": "ringing"}
                })
            except Exception:
                pass

            # Panggil lewat PJSIP
            try:
                result = sip.dial_once(number, RING_TIMEOUT_SEC)
            except Exception as e:
                result = {"answered": False, "duration": 0, "detail": f"error:{e}"}

            print(f"[DIAL] {label} {number} -> answered={result['answered']} ({result['detail']})")

            # Broadcast hasil
            try:
                broadcast_to_clients("/receive-info", {
                    "user": {"id_system": "-", "username": "worker", "phone": "-"},
                    "data": [item],
                    "progress": {
                        "phase": label,
                        "number": number,
                        "answered": result["answered"],
                        "detail": result["detail"]
                    }
                })
            except Exception:
                pass

            if result["answered"]:
                break
            else:
                time.sleep(RETRY_GAP_SEC)

        with state_lock:
            call_status["processed"] += 1
            call_status["in_progress"] = None

        call_queue.task_done()

# Run worker
pause_event.set()
stop_event.clear()
worker_thread = threading.Thread(target=call_flow_worker, daemon=True)
worker_thread.start()

# ===========================================================
#                    API endpoints
# ===========================================================
@app.route("/register-client", methods=["POST"])
def register_client():
    data = request.json or {}
    ip = data.get("ip")
    port = data.get("port", CLIENT_PORT_DEFAULT)
    if not ip:
        return jsonify({"status": "error", "message": "ip diperlukan"}), 400
    base = f"http://{ip}:{port}"
    connected_clients.add(base)
    print(f"✅ Client terdaftar: {base}")
    return jsonify({"status": "ok", "connected_clients": list(connected_clients)}), 200

# body: {
#   "user": {"num_sip":"", "pas_sip":"", ...},
#   "data":[{...}],
#   (opsional) "server_forwarded": true/false
# }
@app.route("/push-data", methods=["POST"])
def push_data():
    payload = request.json or {}
    dataset = payload.get("data", [])
    u = payload.get("user", {}) or {}
    sip_user = u.get("num_sip")
    sip_pass = u.get("pas_sip")

    if not isinstance(dataset, list) or not dataset:
        return jsonify({"status": "error", "message": "data kosong/invalid"}), 400
    if not sip_user or not sip_pass:
        return jsonify({"status": "error", "message": "num_sip/pas_sip kosong"}), 400

    # broadcast ke Windows untuk update tabel (opsional)
    try:
        broadcast_to_clients("/receive-info", payload)
    except Exception as e:
        print(f"[WARN] broadcast awal gagal: {e}")

    # enqueue + sisipkan kredensial
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
    return jsonify({"status": "ok" if code == 200 else "error", "action": action, "message": msg}), code

@app.route("/api/log", methods=["GET"])
def get_status():
    with state_lock:
        s = dict(call_status)
        s["queue_size"] = call_queue.qsize()
    return jsonify(s), 200

# ======= Main =======
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
    finally:
        try:
            sip.destroy()
        except Exception:
            pass
