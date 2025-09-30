import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from flask import Flask, request, jsonify
import requests
import socket
import time
import json

LINUX_SERVER = "http://192.168.88.90:7000"  # ganti IP Linux
CLIENT_PORT = 6000

flask_app = Flask(__name__)
client_ui = None

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        try:
            return socket.gethostbyname(socket.gethostname())
        except:
            return "127.0.0.1"

@flask_app.route("/receive-info", methods=["POST"])
def receive_info():
    data = request.json or {}

    # HANYA forward jika payload batch dari dashboard (punya kredensial) dan belum diforward
    try:
        user = data.get("user") or {}
        has_creds = bool(user.get("num_sip")) and bool(user.get("pas_sip"))
        is_batch = has_creds and isinstance(data.get("data"), list) and len(data["data"]) > 0
        already_forwarded = bool(data.get("server_forwarded"))

        if is_batch and not already_forwarded:
            fwd = dict(data)
            fwd["server_forwarded"] = True
            res = requests.post(f"{LINUX_SERVER}/push-data", json=fwd, timeout=10)
            msg = f"[FORWARD] /push-data -> {res.status_code} {res.text[:200]}\n"
            if client_ui:
                client_ui.log_area.insert("end", msg)
                client_ui.log_area.see("end")
    except Exception as e:
        if client_ui:
            client_ui.log_area.insert("end", f"[FORWARD ERROR] {e}\n")
            client_ui.log_area.see("end")

    # Tampilkan event/progress apapun ke UI
    if client_ui:
        client_ui.show_data(data)

    return jsonify({"status": "ok", "message": "Data diterima client"}), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=CLIENT_PORT, debug=False, use_reloader=False)

class ClientUI:
    def __init__(self, root):
        self.root = root
        self.root.title("📞 Client Info Call")
        self.root.geometry("980x860")
        self.root.configure(bg="#e9f5ee")

        # cache untuk staff info & dataset utama dari dashboard
        self.staff_user = {}
        self.data_list = []

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 10), background="#e9f5ee")
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"),
                        background="#4caf50", foreground="white", padding=6)
        style.configure("Card.TFrame", background="white", relief="raised", borderwidth=2)

        header = ttk.Label(self.root, text="📞 Data Diterima dari Server",
                           style="Header.TLabel", anchor="center")
        header.pack(fill="x", pady=5)

        # --- Kartu User ---
        self.user_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        self.user_frame.pack(fill="x", padx=12, pady=10)
        self.user_label = ttk.Label(self.user_frame, text="👤 Staff Information",
                                    font=("Segoe UI", 11, "bold"), background="white")
        self.user_label.pack(anchor="w")
        self.user_info = ttk.Label(self.user_frame, text="-", justify="left", background="white")
        self.user_info.pack(anchor="w", pady=5)

        # --- Tabel Data Nasabah ---
        self.data_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        self.data_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.data_label = ttk.Label(self.data_frame, text="📊 DATA NASABAH",
                                    font=("Segoe UI", 11, "bold"), background="white")
        self.data_label.pack(anchor="w")

        tree_frame = ttk.Frame(self.data_frame)
        tree_frame.pack(fill="both", expand=True, pady=5)

        self.tree = ttk.Treeview(tree_frame,
                                 columns=("nama", "phone", "ec_name_1", "ec_phone_1",
                                          "ec_name_2", "ec_phone_2", "tagihan"),
                                 show="headings", height=11)
        for col, text in zip(self.tree["columns"],
                             ["Nama Nasabah","Phone","Nama EC 1","Phone EC 1","Nama EC 2","Phone EC 2","Total Tagihan"]):
            self.tree.heading(col, text=text)
        self.tree.column("nama", width=200, anchor="w")
        self.tree.column("phone", width=150, anchor="center")
        self.tree.column("ec_name_1", width=160, anchor="w")
        self.tree.column("ec_phone_1", width=150, anchor="center")
        self.tree.column("ec_name_2", width=160, anchor="w")
        self.tree.column("ec_phone_2", width=150, anchor="center")
        self.tree.column("tagihan", width=150, anchor="e")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("oddrow", background="#f9f9f9")
        self.tree.tag_configure("evenrow", background="#ffffff")

        # --- Section: Sedang Menelepon (kartu ringkas) ---
        self.now_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        self.now_frame.pack(fill="x", padx=12, pady=(0,10))
        ttk.Label(self.now_frame, text="📞 Sedang Menelepon",
                  font=("Segoe UI", 11, "bold"), background="white").pack(anchor="w")
        self.now_info = ttk.Label(self.now_frame, text="-", justify="left", background="white")
        self.now_info.pack(anchor="w", pady=5)

        # --- Tombol kontrol ---
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill="x")
        self.call_btn  = ttk.Button(btn_frame, text="📞 Call",  command=lambda: self.send_command("call"))
        self.pause_btn = ttk.Button(btn_frame, text="⏸ Pause", command=lambda: self.send_command("pause"))
        self.start_btn = ttk.Button(btn_frame, text="▶ Start", command=lambda: self.send_command("start"))
        self.stop_btn  = ttk.Button(btn_frame, text="⏹ Stop",  command=lambda: self.send_command("stop"))
        for b in (self.call_btn, self.pause_btn, self.start_btn, self.stop_btn):
            b.pack(side="left", padx=5)

        # --- Log ---
        log_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        log_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.log_label = ttk.Label(log_frame, text="📝 Monitoring Log",
                                   font=("Segoe UI", 11, "bold"), background="white")
        self.log_label.pack(anchor="w")
        self.log_area = scrolledtext.ScrolledText(log_frame, height=10, wrap="word",
                                                  font=("Consolas", 10), background="black", foreground="lime")
        self.log_area.pack(fill="both", expand=True, pady=5)

        # --- Status bar (poll /api/log) ---
        self.status_var = tk.StringVar(value="Status: -")
        self.status_label = ttk.Label(self.root, textvariable=self.status_var)
        self.status_label.pack(fill="x", padx=12, pady=(0,8))

        # Workers
        threading.Thread(target=self.register_to_server, daemon=True).start()
        threading.Thread(target=self.poll_server_status, daemon=True).start()
        threading.Thread(target=self.poll_server_events, daemon=True).start()

    # ---------- helper UI ----------
    def _refresh_user_card(self):
        u = self.staff_user or {}
        text = (
            f"ID: {u.get('id_system','-')}\n"
            f"Username: {u.get('username','-')}\n"
            f"Phone: {u.get('phone','-')}\n"
            f"Email: {u.get('email','-')}\n"
            f"SIP: {u.get('num_sip','-')}"
        )
        self.user_info.config(text=text)

    def _refresh_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, d in enumerate(self.data_list):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.tree.insert("", "end",
                             values=(d.get('nama_nasabah',''), d.get('phone',''),
                                     d.get('ec_name_1',''), d.get('ec_phone_1',''),
                                     d.get('ec_name_2',''), d.get('ec_phone_2',''),
                                     d.get('total_tagihan','')),
                             tags=(tag,))

    # ---------- Tampilkan data + progress ----------
    def show_data(self, data):
        # Jika datang batch asli dari dashboard (ada data list & kredensial),
        # simpan ke cache dan refresh UI utama. EVENT progress dari server tidak memodifikasi ini.
        if isinstance(data.get("data"), list) and data.get("data"):
            user = data.get("user") or {}
            if user.get("num_sip") and user.get("pas_sip"):
                self.staff_user = {
                    "id_system": user.get("id_system"),
                    "username": user.get("username"),
                    "phone": user.get("phone"),
                    "email": user.get("email"),
                    "num_sip": user.get("num_sip"),
                }
                self.data_list = list(data["data"])
                self._refresh_user_card()
                self._refresh_table()

        # Section "Sedang Menelepon" + Log dari progress
        progress = data.get("progress")
        if progress:
            phase = progress.get('phase', '-')
            number = progress.get('number', '-')
            answered = progress.get('answered')  # None saat CALLING
            detail = progress.get('detail', '')

            # Update kartu "Sedang Menelepon" (tidak memodifikasi Staff/Data Nasabah)
            current_text = f"Phase: {phase}\nNomor: {number}\nStatus: "
            current_text += ("Memanggil..." if answered is None else f"answered={answered} ({detail})")
            self.now_info.config(text=current_text)

            # Log
            if answered is None:
                msg = f"[CALL] {phase} -> {number} | (sedang menelepon…)\n"
            else:
                msg = f"[CALL] {phase} -> {number} | answered={answered} ({detail})\n"
            self.log_area.insert("end", msg)
            self.log_area.see("end")

    # ---------- Polling status server (/api/log) ----------
    def poll_server_status(self):
        last_inprog_key = None
        while True:
            try:
                r = requests.get(f"{LINUX_SERVER}/api/log", timeout=4)
                if r.status_code == 200:
                    st = r.json()
                    running = st.get("running")
                    paused  = st.get("paused")
                    qsize   = st.get("queue_size", 0)
                    inprog  = st.get("in_progress") or {}

                    number = "-"
                    if inprog:
                        number = inprog.get("phone") or inprog.get("ec_phone_1") or inprog.get("ec_phone_2") or "-"

                    state = "Running" if running else "Stopped"
                    if running and paused:
                        state = "Paused"

                    self.status_var.set(f"Status: {state} | Queue: {qsize} | DIALING: {number}")

                    cur_key = json.dumps(inprog, sort_keys=True) if inprog else ""
                    if inprog and cur_key != last_inprog_key:
                        # Jangan ubah tabel dan staff info di sini
                        self.now_info.config(text=f"Phase: Menyiapkan panggilan\nNomor: {number}\nStatus: -")
                        self.log_area.insert("end", f"[CALL] Sedang menelepon -> {number}\n")
                        self.log_area.see("end")
                        last_inprog_key = cur_key
                else:
                    self.status_var.set(f"Status: (server {r.status_code})")
            except Exception:
                pass
            time.sleep(1.5)

    # ---------- Polling event stream (/events) ----------
    def poll_server_events(self):
        since = 0
        while True:
            try:
                r = requests.get(f"{LINUX_SERVER}/events", params={"since": since}, timeout=6)
                if r.status_code == 200:
                    data = r.json()
                    evs = data.get("events", [])
                    if evs:
                        for e in evs:
                            etype = e.get("type")
                            if etype == "progress":
                                payload = e.get("payload", {})
                                prog = payload.get("progress", {})
                                phase = prog.get("phase", "-")
                                number = prog.get("number", "-")
                                answered = prog.get("answered")
                                detail = prog.get("detail", "")

                                # Update kartu "Sedang Menelepon"
                                current_text = f"Phase: {phase}\nNomor: {number}\nStatus: "
                                current_text += ("Memanggil..." if answered is None else f"answered={answered} ({detail})")
                                self.now_info.config(text=current_text)

                                # Log
                                if answered is None:
                                    msg = f"[CALL] {phase} -> {number} | (sedang menelepon…)\n"
                                else:
                                    msg = f"[CALL] {phase} -> {number} | answered={answered} ({detail})\n"
                                self.log_area.insert("end", msg)
                                self.log_area.see("end")

                            elif etype == "action":
                                act = e.get("payload", {})
                                self.log_area.insert("end", f"[ACTION] {act.get('action')} -> {act.get('message')}\n")
                                self.log_area.see("end")

                            elif etype == "dataset":
                                # Hanya jika dataset dari dashboard (punya kredensial), refresh tabel + staff.
                                payload = e.get("payload", {})
                                usr = payload.get("user") or {}
                                if usr.get("num_sip") and usr.get("pas_sip"):
                                    self.staff_user = {
                                        "id_system": usr.get("id_system"),
                                        "username": usr.get("username"),
                                        "phone": usr.get("phone"),
                                        "email": usr.get("email"),
                                        "num_sip": usr.get("num_sip"),
                                    }
                                    self.data_list = list(payload.get("data") or [])
                                    self._refresh_user_card()
                                    self._refresh_table()
                                self.log_area.insert("end", "[INFO] Dataset diterima server.\n")
                                self.log_area.see("end")
                        since = max(since, max(ev["event_id"] for ev in evs))
                    else:
                        since = data.get("last_id", since)
            except Exception:
                pass
            time.sleep(0.8)

    # ---------- Kirim perintah ke Server Linux ----------
    def send_command(self, action):
        try:
            url = f"{LINUX_SERVER}/api/{action}"
            res = requests.post(url, timeout=5)
            if res.status_code == 200:
                j = res.json()
                msg = f"[OK] {action.upper()} -> {j.get('message')}\n"
            else:
                msg = f"[ERROR] {action.upper()} gagal ({res.status_code})\n"
        except Exception as e:
            msg = f"[EXCEPTION] {action.upper()} -> {str(e)}\n"
        self.log_area.insert("end", msg)
        self.log_area.see("end")

    # ---------- Registrasi ke Server Linux ----------
    def register_to_server(self):
        local_ip = get_local_ip()
        try:
            res = requests.post(f"{LINUX_SERVER}/register-client",
                                json={"ip": local_ip, "port": CLIENT_PORT},
                                timeout=5)
            if res.status_code == 200:
                self.log_area.insert("end", f"[INFO] Terdaftar ke server Linux -> {res.json()}\n")
            else:
                self.log_area.insert("end", f"[ERROR] Gagal register ke server ({res.status_code})\n")
        except Exception as e:
            self.log_area.insert("end", f"[EXCEPTION] Register -> {str(e)}\n")
        self.log_area.see("end")

def run_flask():
    flask_app.run(host="0.0.0.0", port=CLIENT_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    root = tk.Tk()
    client_ui = ClientUI(root)
    client_ui.user_info.config(text="-")
    root.mainloop()
