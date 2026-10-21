import socket
import time
from datetime import datetime

HOST = "10.0.0.2"
PORT = 5005
INTERVAL = 1.0
TOTAL_PACKETS = 10

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

try:
    for seq in range(1, TOTAL_PACKETS + 1):
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        message = f"Packet {seq}, sent at {timestamp}"

        sock.sendto(message.encode(), (HOST, PORT))
        print(f"Sent: {message}")

        time.sleep(INTERVAL)
finally:
    sock.close()
