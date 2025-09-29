#!/usr/bin/env python3
import threading
import time
from queue import Queue, Empty
from collections import deque
from flask import Flask, jsonify, request as flask_request
import requests

# ==== PJSIP (pjsua) ====
import pjsua as pj

# ======================= Konfigurasi =======================
PORT = 7000
RING_TIMEOUT_SEC = 45
RETRY_GAP_SEC = 4
CLIENT_PORT_DEFAULT = 6000

# SIP server kamu (ganti jika perlu)
SIP_DOMAIN = "ld.infin8link.com"
SIP_HOSTPORT = "ld.infin8link.com:7060"
SIP_REG_URI = f"sip:{SIP_HOSTPORT}"
SIP_DIAL_SUFFIX = f"@{SIP_HOSTPORT}"   # sip:<number>@ld.infin8link.com:7060

# Waktu tunggu antar langkah bridge (detik)
DIAL_WAIT_STEP = 0.1
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
stop_event  = threading.Event()  # set() -> stop (abort aktif)
run_event   = threading.Event()  # set() -> worker boleh eksekusi (set saat /api/call)

# ======= Event Bus (untuk realtime polling dari Windows) =======
EVENT_MAX = 2000
events_buf = deque(maxlen=EVENT_MAX)  # simpan dict event
event_lock = threading.Lock()
event_seq = 0

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

def publish_event(ev: dict, also_broadcast=True):
    """
    Simpan event ke buffer + optional broadcast ke Windows.
    ev: { "type": "...", "payload": {...} }
    """
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

def make_progress_payload(item, phase, number, answered, detail):
    return {
        "user": {"id_system": "-", "username": "worker", "phone": "-"},
        "data": [item],
        "progress": {
            "phase": phase,
            "number": number,
            "answered": answered,  # None saat "CALLING ..."
            "detail": detail
        }
    }

# ===========================================================
#                 PJSIP: Library & Account
# ===========================================================
def _log_cb(level, s, length):
    try:
        print(s.strip())
    except Exception:
        pass

def _register_pj_thread(name="worker"):
    """Wajib dipanggil di setiap thread non-utama yang menyentuh PJLIB/PJSUA."""
    try:
        pj.Lib.instance().thread_register(name)
    except Exception:
        pass

class _CallCb(pj.CallCallback):
    """Callback untuk setiap panggilan (agent atau nasabah)."""
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
        # tracking leg aktif
        self.active_calls = set()
        self.active_lock = threading.Lock()

    def _init_lib(self):
        self.lib = pj.Lib()
        self.lib.init(log_cfg=pj.LogConfig(level=2, callback=_log_cb))
        # transport UDP & TCP
        self.lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(0))
        self.lib.create_transport(pj.TransportType.TCP, pj.TransportConfig(0))
        self.lib.start()
        # Nonaktifkan audio device (server headless); media tetap bisa via conference port
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
        # pastikan thread ini sudah terdaftar di PJLIB
        _register_pj_thread("ensure-account")

        with self.lock:
            if self.acc and self.acc_user == username and self.acc_pass == password:
                return
            self._destroy_acc()
            cfg = pj.AccountConfig()
            cfg.id = f"sip:{username}@{SIP_DOMAIN}"
            cfg.reg_uri = SIP_REG_URI
            # Penting: gunakan argumen POSISI (realm, username, passwd)
            cfg.auth_cred = [pj.AuthCred("*", username, password)]
            # Jika perlu paksa UDP:
            # cfg.proxy = [f"sip:{SIP_HOSTPORT};transport=udp"]

            self.acc = self.lib.create_account(cfg)
            self.acc_user = username
            self.acc_pass = password

            # Tunggu register (maks 5 detik)
            t0 = time.time()
            while time.time() - t0 < 5:
                info = self.acc.info()
                if info.reg_status == 200:
                    print(f"[PJSIP] Registered as {username}")
                    return
                time.sleep(0.2)
            print(f"[PJSIP] Register pending/failed: {self.acc.info().reg_status}")

    def _track_call(self, call, add=True):
        with self.active_lock:
            if add:
                self.active_calls.add(call)
            else:
                try:
                    self.active_calls.discard(call)
                except Exception:
                    pass

    def hangup_all(self):
        """Putuskan semua leg aktif segera (untuk STOP total)."""
        try:
            pj.Lib.instance().hangup_all()
        except Exception:
            pass
        with self.active_lock:
            for c in list(self.active_calls):
                try:
                    c.hangup()
                except Exception:
                    pass
            self.active_calls.clear()

    # -------------------- 3PCC Bridge --------------------
    def bridge_agent_with_peer(self, agent_user: str, peer_number: str, ring_timeout_sec: int):
        """
        3PCC:
          1) Panggil Agent (sip:<agent_user>@HOSTPORT) -> tunggu jawab
          2) Panggil Peer (sip:<peer_number>@HOSTPORT) -> tunggu jawab
          3) Hubungkan conf_slot keduanya (dua arah)
        Hormati stop_event: jika STOP, hangup semua dan return aborted.
        """
        _register_pj_thread("bridge-3pcc")
        if not self.acc:
            return {"ok": False, "reason": "no_account"}

        # --- 1) Call agent ---
        agent_uri = f"sip:{agent_user}{SIP_DIAL_SUFFIX}"
        a_ans = threading.Event()
        a_disc = threading.Event()
        a_cb = _CallCb(a_ans, a_disc)
        a_call = self.acc.make_call(agent_uri, a_cb)
        self._track_call(a_call, True)

        t0 = time.time()
        while time.time() - t0 < ring_timeout_sec:
            if stop_event.is_set():
                self.hangup_all()
                return {"ok": False, "reason": "aborted"}
            if a_ans.is_set():
                break
            if a_disc.is_set():
                return {"ok": False, "reason": "agent_disconnected"}
            time.sleep(DIAL_WAIT_STEP)
        if not a_ans.is_set():
            self._track_call(a_call, False)
            try: a_call.hangup()
            except: pass
            return {"ok": False, "reason": "agent_no_answer"}

        # --- 2) Call peer (nasabah) ---
        peer_uri = f"sip:{peer_number}{SIP_DIAL_SUFFIX}"
        p_ans = threading.Event()
        p_disc = threading.Event()
        p_cb = _CallCb(p_ans, p_disc)
        p_call = self.acc.make_call(peer_uri, p_cb)
        self._track_call(p_call, True)

        t1 = time.time()
        while time.time() - t1 < ring_timeout_sec:
            if stop_event.is_set():
                self.hangup_all()
                return {"ok": False, "reason": "aborted"}
            if p_ans.is_set():
                break
            if p_disc.is_set():
                # peer putus sebelum jawab
                try: p_call.hangup()
                except: pass
                self._track_call(p_call, False)
                return {"ok": False, "reason": "peer_disconnected"}
            time.sleep(DIAL_WAIT_STEP)
        if not p_ans.is_set():
            try: p_call.hangup()
            except: pass
            self._track_call(p_call, False)
            return {"ok": False, "reason": "peer_no_answer"}

        # --- 3) Bridge media dua arah ---
        try:
            a_slot = a_call.info().conf_slot
            p_slot = p_call.info().conf_slot
            pj.Lib.instance().conf_connect(a_slot, p_slot)
            pj.Lib.instance().conf_connect(p_slot, a_slot)
            return {"ok": True, "reason": "bridged", "a_call": a_call, "p_call": p_call}
        except Exception as e:
            # gagal bridge → putuskan
            self.hangup_all()
            return {"ok": False, "reason": f"bridge_error:{e}"}

sip = SipManager()

# ===========================================================
#                     Worker antrian
# ===========================================================
def call_flow_worker():
    _register_pj_thread("call-worker")

    while True:
        try:
            item = call_queue.get(timeout=0.5)
        except Empty:
            continue

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

        # === Gate: tunggu user klik "Call" ===
        while not run_event.is_set():
            if stop_event.is_set():
                break
            time.sleep(0.2)
        if stop_event.is_set():
            call_queue.task_done()
            with state_lock:
                call_status["in_progress"] = None
            continue

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

        # Urutan panggilan: 3PCC bridge (Agent <-> NASABAH), lalu EC1, EC2 (opsional tanpa bridge)
        numbers = [
            ("NASABAH", item.get("phone")),
            ("EC1", item.get("ec_phone_1")),
            ("EC2", item.get("ec_phone_2")),
        ]

        # NASABAH via BRIDGE ke agent
        label, number = numbers[0]
        if number:
            payload_calling = make_progress_payload(item, f"CALLING {label}", number, None, "ringing")
            publish_event({"type": "progress", "payload": payload_calling})

            result = sip.bridge_agent_with_peer(agent_user=username, peer_number=number,
                                                ring_timeout_sec=RING_TIMEOUT_SEC)
            answered = result.get("ok", False)
            detail = result.get("reason", "")
            publish_event({"type": "progress", "payload": make_progress_payload(item, label, number, answered, detail)})

            # Jika bridged berhasil, akhiri proses item ini (agent ngobrol dengan nasabah)
            if answered:
                with state_lock:
                    call_status["processed"] += 1
                    call_status["in_progress"] = None
                call_queue.task_done()
                # tunggu sampai stop atau sampai kedua leg putus? (Tidak perlu; bridge jalan di background)
                continue
            else:
                # jika tidak terjawab atau gagal bridge, lanjut ke EC1/EC2
                time.sleep(RETRY_GAP_SEC)

        # EC1 dan EC2 — panggilan 1 leg saja (tanpa bridge), hanya untuk pemberitahuan
        for label, number in numbers[1:]:
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

            publish_event({"type": "progress",
                           "payload": make_progress_payload(item, f"CALLING {label}", number, None, "ringing")})

            # panggil 1 leg saja (menunggu jawaban atau timeout), lalu tutup
            ok = single_leg_call(number)
            publish_event({"type": "progress",
                           "payload": make_progress_payload(item, label, number, ok["answered"], ok["detail"])})
            if ok["answered"]:
                break
            time.sleep(RETRY_GAP_SEC)

        with state_lock:
            call_status["processed"] += 1
            call_status["in_progress"] = None
        call_queue.task_done()

def single_leg_call(number: str):
    """Panggilan 1 leg (untuk EC)."""
    _register_pj_thread("single-leg")
    if not sip.acc:
        return {"answered": False, "detail": "no_account"}

    uri = f"sip:{number}{SIP_DIAL_SUFFIX}"
    ans = threading.Event()
    disc = threading.Event()
    cb = _CallCb(ans, disc)
    call = sip.acc.make_call(uri, cb)
    sip._track_call(call, True)

    t0 = time.time()
    answered = False
    while time.time() - t0 < RING_TIMEOUT_SEC:
        if stop_event.is_set():
            try: call.hangup()
            except: pass
            sip._track_call(call, False)
            return {"answered": False, "detail": "aborted"}
        if ans.is_set():
            answered = True
            break
        if disc.is_set():
            break
        time.sleep(DIAL_WAIT_STEP)

    try:
        call.hangup()
    except Exception:
        pass
    sip._track_call(call, False)
    if answered and disc.is_set():
        # kalau langsung putus, tetap dianggap answered=True detail "disconnected"
        return {"answered": True, "detail": "disconnected"}
    return {"answered": answered, "detail": "answered" if answered else "timeout"}

# Inisialisasi flags & jalankan worker
pause_event.set()
stop_event.clear()
run_event.clear()     # default: belum boleh jalan sampai klik "Call"
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

    # event dataset masuk (juga broadcast ke Windows)
    publish_event({"type": "dataset", "payload": payload})

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
        run_event.set()        # mulai eksekusi
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
            call_status["running"] = False
            call_status["paused"] = False
            call_status["stopped"] = True
        run_event.clear()
        stop_event.set()
        pause_event.set()

        # Putuskan SEMUA panggilan aktif SEKARANG
        try:
            sip.hangup_all()
        except Exception:
            pass

        # kosongkan antrian
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
