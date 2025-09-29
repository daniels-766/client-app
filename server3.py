import threading
import time
from queue import Queue
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# =======================
# Konfigurasi
# =======================
PORT = 7000
RING_TIMEOUT_SEC = 45       # waktu tunggu panggilan
RETRY_GAP_SEC = 4           # jeda antar panggilan jika tidak terjawab
CLIENT_PORT_DEFAULT = 6000  # default port client Windows

# =======================
# State global
# =======================
connected_clients = set()   # simpan base URL client, contoh: "http://192.168.88.123:6000"
call_queue = Queue()
state_lock = threading.Lock()

call_status = {
    "running": False,
    "paused": False,
    "stopped": False,
    "in_progress": None,     # info item yang sedang diproses
    "processed": 0,          # counter item selesai
    "queued": 0              # snapshot jumlah enqueued terakhir
}

# Event untuk kontrol worker
pause_event = threading.Event()  # ketika set() -> berjalan, ketika clear() -> pause
stop_event = threading.Event()   # ketika set() -> minta stop

# =======================
# Util: kirim ke semua client Windows agar UI update
# =======================
def broadcast_to_clients(path, payload):
    dead = []
    for base in list(connected_clients):
        url = f"{base}{path}"
        try:
            requests.post(url, json=payload, timeout=3)
        except Exception:
            dead.append(base)
    # bersihkan client yang mati
    for d in dead:
        connected_clients.discard(d)

# =======================
# Simulasi DIAL (GANTI BAGIAN INI DENGAN INTEGRASI DIALER NYATA)
# Return dict: {"answered": bool, "duration": int, "detail": "..."}
# =======================
def dial(phone_number: str, ring_timeout=RING_TIMEOUT_SEC):
    """
    TODO (integrasi nyata):
      - Asterisk ARI/AMI, FreeSWITCH ESL/REST, atau SIP gateway internal.
      - Panggil API, tunggu event (ringing/answered/failed) dengan timeout.
    """
    # --- SIMULASI ---
    # anggap 30% terjawab, sisanya tidak
    import random
    time.sleep(min(3, ring_timeout))  # simulasi waktu tunggu singkat biar cepat dites
    answered = random.random() < 0.30
    return {"answered": answered, "duration": 10 if answered else 0, "detail": "simulated"}

# =======================
# Worker: proses antrian call
# =======================
def call_flow_worker():
    while True:
        # Tunggu item berikut
        item = call_queue.get()
        if item is None:
            # sentinel untuk penghentian penuh (opsional)
            break

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

        # Pause handling
        while not pause_event.is_set():
            time.sleep(0.2)
            if stop_event.is_set():
                break

        if stop_event.is_set():
            call_queue.task_done()
            with state_lock:
                call_status["in_progress"] = None
            continue

        # === Urutan panggilan ===
        # 1) Nasabah
        numbers = [
            ("NASABAH", item.get("phone")),
            ("EC1", item.get("ec_phone_1")),
            ("EC2", item.get("ec_phone_2")),
        ]

        for label, number in numbers:
            # Skip jika nomor kosong
            if not number:
                continue

            # Cek pause/stop sebelum panggilan
            if stop_event.is_set():
                break
            while not pause_event.is_set():
                time.sleep(0.2)
                if stop_event.is_set():
                    break
            if stop_event.is_set():
                break

            result = dial(number, RING_TIMEOUT_SEC)
            logline = {
                "phase": label,
                "number": number,
                "answered": result["answered"],
                "detail": result["detail"]
            }
            print(f"[DIAL] {label} {number} -> answered={result['answered']} ({result['detail']})")

            # Broadcast progress ke UI (opsional)
            try:
                broadcast_to_clients("/receive-info", {
                    "user": {"id_system": "-", "username": "worker", "phone": "-"},
                    "data": [item],
                    "progress": logline
                })
            except Exception:
                pass

            if result["answered"]:
                # Jika terjawab, berhenti di sini (anggap ngobrol diluar workflow ini)
                break
            else:
                # Tidak terjawab -> jeda sebelum target berikutnya
                time.sleep(RETRY_GAP_SEC)

        # Selesai satu item
        with state_lock:
            call_status["processed"] += 1
            call_status["in_progress"] = None

        call_queue.task_done()

# Jalankan worker thread
pause_event.set()  # default: berjalan
stop_event.clear()
worker_thread = threading.Thread(target=call_flow_worker, daemon=True)
worker_thread.start()

# =======================
# API: registrasi client Windows
# =======================
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

# =======================
# API: terima data dari dashboard & enqueue
# body: { "user": {...}, "data": [ {nama_nasabah, phone, ec_name_1, ec_phone_1, ec_name_2, ec_phone_2, total_tagihan}, ... ] }
# =======================
@app.route("/push-data", methods=["POST"])
def push_data():
    payload = request.json or {}
    dataset = payload.get("data", [])
    if not isinstance(dataset, list) or not dataset:
        return jsonify({"status": "error", "message": "data kosong/invalid"}), 400

    # broadcast ke client Windows untuk tampilan
    try:
        broadcast_to_clients("/receive-info", payload)
    except Exception as e:
        print(f"[WARN] broadcast gagal: {e}")

    # masukkan ke antrian panggilan
    added = 0
    for row in dataset:
        call_queue.put(row)
        added += 1

    with state_lock:
        call_status["queued"] += added

    return jsonify({"status": "ok", "enqueued": added, "queue_size": call_queue.qsize()}), 200

# =======================
# API: kontrol (call/start/pause/stop) dari Windows client
# =======================
@app.route("/api/<action>", methods=["POST"])
def handle_action(action):
    msg = "Action tidak dikenal"
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

    elif action == "start":  # resume
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
                # meski belum running, tetap set stop agar worker skip item berikutnya
                call_status["stopped"] = True

        stop_event.set()
        pause_event.set()  # biar worker tidak “diam” saat di-pause
        # kosongkan antrian agar benar-benar berhenti batch saat ini selesai
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
        code = 400

    print(f"[ACTION] {action.upper()} -> {msg}")
    return jsonify({"status": "ok" if code == 200 else "error", "action": action, "message": msg}), code

# =======================
# API: status/monitor
# =======================
@app.route("/api/log", methods=["GET"])
def get_status():
    with state_lock:
        s = dict(call_status)  # shallow copy
        s["queue_size"] = call_queue.qsize()
    return jsonify(s), 200

# =======================
# Main
# =======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
