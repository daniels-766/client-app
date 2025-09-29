# server.py (Linux Server)
import asyncio
import websockets
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

# List client websocket yang connect
clients = set()

# WebSocket Server untuk broadcast log
async def ws_handler(websocket, path):
    clients.add(websocket)
    try:
        async for _ in websocket:
            pass  # kita tidak terima pesan dari client, hanya kirim
    finally:
        clients.remove(websocket)

async def broadcast(message: str):
    if clients:
        await asyncio.wait([client.send(message) for client in clients])

# API untuk test call
@app.route("/test-call", methods=["POST"])
def test_call():
    data = request.json
    log_msg = f"📞 Test Call dari user: {data}"
    print(log_msg)

    # kirim ke semua client websocket
    asyncio.run(broadcast(log_msg))

    return jsonify({"status": "ok", "message": log_msg})

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False)

def run_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ws_server = websockets.serve(ws_handler, "0.0.0.0", 6789)
    loop.run_until_complete(ws_server)
    loop.run_forever()

if __name__ == "__main__":
    t1 = threading.Thread(target=run_flask, daemon=True)
    t1.start()

    run_websocket()
