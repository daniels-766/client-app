# client.py (Windows Client)
import tkinter as tk
from tkinter import scrolledtext
import threading
import asyncio
import websockets

class ClientUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Client Log Viewer")
        self.root.geometry("600x400")

        self.log_area = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, width=70, height=20, font=("Consolas", 10))
        self.log_area.pack(padx=10, pady=10)

    def show_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)

async def listen_ws(ui: ClientUI):
    uri = "ws://10.0.2.15:6789"  # ganti IP server Linux
    async with websockets.connect(uri) as websocket:
        while True:
            message = await websocket.recv()
            ui.show_log(message)

def start_ws_loop(ui: ClientUI):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(listen_ws(ui))

if __name__ == "__main__":
    root = tk.Tk()
    ui = ClientUI(root)

    t = threading.Thread(target=start_ws_loop, args=(ui,), daemon=True)
    t.start()

    root.mainloop()
