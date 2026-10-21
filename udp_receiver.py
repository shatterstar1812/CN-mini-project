import socket
from datetime import datetime

HOST = "0.0.0.0"
PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))

print(f"UDP receiver listening on port {PORT}")

count = 0

try:
    while True:
        data, addr = sock.recvfrom(1024)
        count += 1
        timestamp = datetime.now().strftime("%H:%M:%S.%f")

        print(
            f"[{timestamp}] Packet {count} "
            f"from {addr}: {data.decode(errors='replace')}"
        )
except KeyboardInterrupt:
    print(f"\nTotal packets received: {count}")
finally:
    sock.close()
