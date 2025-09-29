import threading
import tkinter as tk
from tkinter import ttk
from flask import Flask, request, jsonify

flask_app = Flask(__name__)
client_ui = None

@flask_app.route("/receive-info", methods=["POST"])
def receive_info():
    data = request.json
    print("📩 Data diterima:", data)

    if client_ui:
        client_ui.show_data(data)

    return jsonify({"status": "ok", "message": "Data diterima client"}), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=6000, debug=False, use_reloader=False)

class ClientUI:
    def __init__(self, root):
        self.root = root
        self.root.title("📞 Client Info Call")
        self.root.geometry("800x550")
        self.root.configure(bg="#e9f5ee")

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

        self.tree.heading("nama", text="Nama Nasabah")
        self.tree.heading("phone", text="Phone")
        self.tree.heading("ec_name_1", text="Nama EC 1")
        self.tree.heading("ec_phone_1", text="Phone EC 1")
        self.tree.heading("ec_name_2", text="Nama EC 2")
        self.tree.heading("ec_phone_2", text="Phone EC 2")
        self.tree.heading("tagihan", text="Total Tagihan")

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

    def show_data(self, data):
        user = data.get("user", {})
        user_text = f"ID: {user.get('id_system')}\nUsername: {user.get('username')}\nPhone: {user.get('phone')}"
        self.user_info.config(text=user_text)

        for i in self.tree.get_children():
            self.tree.delete(i)

        for idx, d in enumerate(data.get("data", [])):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.tree.insert(
                "",
                "end",
                values=(
                    d['nama_nasabah'],
                    d['phone'],
                    d['ec_name_1'],
                    d['ec_phone_1'],
                    d['ec_name_2'],
                    d['ec_phone_2'],
                    d['total_tagihan']
                ),
                tags=(tag,)
            )

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    root = tk.Tk()
    client_ui = ClientUI(root)
    root.mainloop()
