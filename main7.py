import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from flask import Flask, request, jsonify
import requests
import socket

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
    data = request.json

    # Jika datang dari dashboard, forward ke Linux (sekali saja)
    if data and data.get("origin") == "dashboard" and not data.get("server_forwarded"):
        try:
            fwd = dict(data)
            fwd["server_forwarded"] = True
            requests.post(f"{LINUX_SERVER}/push-data", json=fwd, timeout=10)
        except Exception as e:
            print("[FORWARD ERROR]", e)

    print("📩 Data diterima di Windows:", data)
    if client_ui:
        client_ui.show_data(data)
    return jsonify({"status": "ok", "message": "Data diterima client"}), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=CLIENT_PORT, debug=False, use_reloader=False)

class ClientUI:
    def __init__(self, root):
        self.root = root
        self.root.title("📞 Client Info Call")
        self.root.geometry("900x650")
        self.root.configure(bg="#e9f5ee")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 10), background="#e9f5ee")
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"),
                        background="#4caf50", foreground="white", padding=6)
        style.configure("Card.TFrame", background="white", relief="raised", borderwidth=2)

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
                                 show="headings", height=10)
        for col, text in zip(self.tree["columns"], ["Nama Nasabah","Phone","Nama EC 1","Phone EC 1",
                                                    "Nama EC 2","Phone EC 2","Total Tagihan"]):
            self.tree.heading(col, text=text)
        self.tree.column("nama", width=200, anchor="w")
        self.tree.column("phone", width=120, anchor="center")
        self.tree.column("ec_name_1", width=150, anchor="w")
        self.tree.column("ec_phone_1", width=120, anchor="center")
        self.tree.column("ec_name_2", width=150, anchor="w")
        self.tree.column("ec_phone_2", width=120, anchor="center")
        self.tree.column("tagihan", width=120, anchor="e")

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

        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill="x")
        self.call_btn = ttk.Button(btn_frame, text="📞 Call", command=lambda: self.send_command("call"))
        self.call_btn.pack(side="left", padx=5)
        self.pause_btn = ttk.Button(btn_frame, text="⏸ Pause", command=lambda: self.send_command("pause"))
        self.pause_btn.pack(side="left", padx=5)
        self.start_btn = ttk.Button(btn_frame, text="▶ Start", command=lambda: self.send_command("start"))
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="⏹ Stop", command=lambda: self.send_command("stop"))
        self.stop_btn.pack(side="left", padx=5)

        log_frame = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        log_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.log_label = ttk.Label(log_frame, text="📝 Monitoring Log",
                                   font=("Segoe UI", 11, "bold"), background="white")
        self.log_label.pack(anchor="w")
        self.log_area = scrolledtext.ScrolledText(log_frame, height=8, wrap="word",
                                                  font=("Consolas", 10), background="black", foreground="lime")
        self.log_area.pack(fill="both", expand=True, pady=5)

        threading.Thread(target=self.register_to_server, daemon=True).start()

    def show_data(self, data):
        user = data.get("user", {})
        self.user_info.config(
            text=f"ID: {user.get('id_system')}\nUsername: {user.get('username')}\nPhone: {user.get('phone')}"
        )

        # refresh tabel
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, d in enumerate(data.get("data", [])):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.tree.insert("", "end",
                             values=(d.get('nama_nasabah',''), d.get('phone',''),
                                     d.get('ec_name_1',''), d.get('ec_phone_1',''),
                                     d.get('ec_name_2',''), d.get('ec_phone_2',''),
                                     d.get('total_tagihan','')),
                             tags=(tag,))

        # === LOG: tampilkan nomor yang sedang ditelepon / hasilnya ===
        progress = data.get("progress")
        if progress:
            phase = progress.get('phase', '-')
            number = progress.get('number', '-')
            answered = progress.get('answered')
            detail = progress.get('detail', '')
            # answered bisa None saat CALLING
            if answered is None:
                msg = f"[CALL] {phase} -> {number} | (sedang menelepon…)\n"
            else:
                msg = f"[CALL] {phase} -> {number} | answered={answered} ({detail})\n"
            self.log_area.insert("end", msg)
            self.log_area.see("end")

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

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    root = tk.Tk()
    client_ui = ClientUI(root)
    root.mainloop()
