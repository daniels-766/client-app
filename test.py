import threading
import tkinter as tk
from tkinter import ttk
from flask import Flask, request, jsonify
import pjsua as pj
import time

flask_app = Flask(__name__)
client_ui = None

# ---------- SIP CLIENT ----------
class SIPClient:
    def __init__(self):
        self.lib = pj.Lib()
        self.acc = None
        self.running = False
        self.paused = False

    def log_cb(self, level, str_, len_):
        print(str_)

    def start(self, sip_server, domain, username, password):
        try:
            self.lib.init(log_cfg=pj.LogConfig(level=3, callback=self.log_cb))
            transport = self.lib.create_transport(
                pj.TransportType.UDP,
                pj.TransportConfig(0)
            )
            self.lib.start()

            acc_cfg = pj.AccountConfig(domain, username, password)
            acc_cfg.id = f"sip:{username}@{domain}"
            acc_cfg.reg_uri = f"sip:{sip_server}"
            acc_cfg.proxy = [f"sip:{sip_server}"]
            self.acc = self.lib.create_account(acc_cfg)

            print(f"✅ SIP account {username} registered to {sip_server}")
        except pj.Error as e:
            print("SIP Init Error:", str(e))

    def call(self, number, domain):
        if not self.acc:
            print("❌ No SIP account registered")
            return None
        uri = f"sip:{number}@{domain}"
        try:
            call = self.acc.make_call(uri)
            print(f"📞 Calling {uri} ...")
            return call
        except pj.Error as e:
            print("Call Error:", str(e))
            return None

    def stop(self):
        self.running = False
        if self.lib:
            self.lib.destroy()
            self.lib = None

# ---------- FLASK API ----------
@flask_app.route("/receive-info", methods=["POST"])
def receive_info():
    data = request.json
    print("📩 Data diterima:", data)

    if client_ui:
        client_ui.show_data(data)
        client_ui.setup_sip(data.get("user", {}))

    return jsonify({"status": "ok", "message": "Data diterima client"}), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=6000, debug=False, use_reloader=False)

# ---------- TKINTER UI ----------
class ClientUI:
    def __init__(self, root):
        self.root = root
        self.root.title("📞 Client Info Call")
        self.root.geometry("850x600")
        self.root.configure(bg="#e9f5ee")

        self.sip_client = SIPClient()
        self.domain = "ld.infin8link.com:6070"
        self.sip_server = "ld.infin8link.com:6070"

        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TLabel", font=("Segoe UI", 10), background="#e9f5ee")
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"),
                        background="#4caf50", foreground="white", padding=6)
        style.configure("Card.TFrame", background="white", relief="raised", borderwidth=2)
        style.configure("Treeview",
                        font=("Segoe UI", 10),
                        rowheight=26,
                        background="white",
                        fieldbackground="white")
        style.configure("Treeview.Heading",
                        font=("Segoe UI", 10, "bold"),
                        background="#4caf50",
                        foreground="white")
        style.map("Treeview", background=[("selected", "#a5d6a7")])

        header = ttk.Label(self.root, text="📞 Data Diterima dari Server",
                           style="Header.TLabel", anchor="center")
        header.pack(fill="x", pady=5)

        self.user_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        self.user_frame.pack(fill="x", padx=12, pady=10)

        self.user_label = ttk.Label(self.user_frame, text="👤 Staff Information",
                                    font=("Segoe UI", 11, "bold"), background="white")
        self.user_label.pack(anchor="w")

        self.user_info = ttk.Label(self.user_frame, text="", justify="left", background="white")
        self.user_info.pack(anchor="w", pady=5)

        # Buttons
        btn_frame = ttk.Frame(self.user_frame, style="Card.TFrame")
        btn_frame.pack(fill="x", pady=5)

        self.call_btn = ttk.Button(btn_frame, text="▶️ Call", command=self.start_call)
        self.call_btn.pack(side="left", padx=5)

        self.pause_btn = ttk.Button(btn_frame, text="⏸ Pause", command=self.pause_call)
        self.pause_btn.pack(side="left", padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="⏹ Stop", command=self.stop_call)
        self.stop_btn.pack(side="left", padx=5)

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
                                 show="headings", height=12)

        for col, text in [("nama", "Nama Nasabah"), ("phone", "Phone"),
                          ("ec_name_1", "Nama EC 1"), ("ec_phone_1", "Phone EC 1"),
                          ("ec_name_2", "Nama EC 2"), ("ec_phone_2", "Phone EC 2"),
                          ("tagihan", "Total Tagihan")]:
            self.tree.heading(col, text=text)

        self.tree.pack(fill="both", expand=True)

        self.data_list = []

    def setup_sip(self, user):
        username = user.get("num_sip")
        password = user.get("pas_sip")
        if username and password:
            threading.Thread(
                target=self.sip_client.start,
                args=(self.sip_server, self.domain, username, password),
                daemon=True
            ).start()

    def show_data(self, data):
        user = data.get("user", {})
        user_text = (f"ID: {user.get('id_system')}\n"
                     f"Username: {user.get('username')}\n"
                     f"Phone: {user.get('phone')}")
        self.user_info.config(text=user_text)

        for i in self.tree.get_children():
            self.tree.delete(i)

        self.data_list = data.get("data", [])
        for d in self.data_list:
            self.tree.insert("", "end", values=(
                d['nama_nasabah'],
                d['phone'],
                d['ec_name_1'],
                d['ec_phone_1'],
                d['ec_name_2'],
                d['ec_phone_2'],
                d['total_tagihan']
            ))

    def start_call(self):
        def auto_call():
            self.sip_client.running = True
            for nasabah in self.data_list:
                if not self.sip_client.running: break
                for number in [nasabah["phone"], nasabah["ec_phone_1"], nasabah["ec_phone_2"]]:
                    if not number: continue
                    if not self.sip_client.running: break
                    call = self.sip_client.call(number, self.domain)
                    for i in range(45):  # wait 45s
                        if not self.sip_client.running or self.sip_client.paused: break
                        time.sleep(1)
                    print("⏭ Next number...")
                    time.sleep(4)  # delay before next number
        threading.Thread(target=auto_call, daemon=True).start()

    def pause_call(self):
        self.sip_client.paused = not self.sip_client.paused
        print("⏸ Paused" if self.sip_client.paused else "▶️ Resumed")

    def stop_call(self):
        self.sip_client.running = False
        self.sip_client.stop()
        print("⏹ Stopped")

# ---------- MAIN ----------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    root = tk.Tk()
    client_ui = ClientUI(root)
    root.mainloop()
