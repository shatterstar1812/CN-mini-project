
import socket
import struct
import json
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

HEADER = struct.Struct("!4s16sId")
MAGIC = b"UDPD"

HOST = "0.0.0.0"
PORT = 5005

BUFFER_SIZE = 65535
STATS_FILE = "receiver_stats.csv"

runs = {}
completed_runs = set()


def save_stats(row):

    exists = Path(STATS_FILE).exists()

    with open(STATS_FILE, "a", newline="") as file:

        writer = csv.DictWriter(file, fieldnames=row.keys())

        if not exists or Path(STATS_FILE).stat().st_size == 0:
            writer.writeheader()

        writer.writerow(row)


def finish_run(summary, address):

    run_id = summary["run_id"]

    if run_id in completed_runs:
        return

    stats = runs.get(run_id, {
        "sequences": set(),
        "bytes": 0,
        "duplicates": 0,
        "out_of_order": 0,
        "last_sequence": 0
    })

    expected = int(summary["sent_packets"])
    received = len(stats["sequences"])

    lost = max(expected - received, 0)

    loss_percent = (
        lost / expected * 100 if expected else 0
    )

    duration = float(summary["duration_seconds"])

    throughput = (
        stats["bytes"] * 8 / duration / 1000000
        if duration > 0 else 0
    )

    row = {
        "run_id": run_id,
        "sender_address": f"{address[0]}:{address[1]}",
        "expected_packets": expected,
        "received_packets": received,
        "lost_packets": lost,
        "packet_loss_percent": round(loss_percent, 4),
        "received_bytes": stats["bytes"],
        "duration_seconds": round(duration, 4),
        "receiver_throughput_mbps": round(throughput, 6),
        "duplicate_packets": stats["duplicates"],
        "out_of_order_packets": stats["out_of_order"],
        "receiver_end_time": datetime.now(timezone.utc).isoformat()
    }

    save_stats(row)
    completed_runs.add(run_id)

    print("\n========== RECEIVER STATISTICS ==========")

    for key, value in row.items():
        print(f"{key}: {value}")

    print("Saved to:", STATS_FILE)
    print("=========================================\n")


def main():

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:

        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))

        print("UDP Receiver Started")
        print(f"Listening on {HOST}:{PORT}")
        print("Waiting for packets...\n")

        try:

            while True:

                data, address = sock.recvfrom(BUFFER_SIZE)

                # Check end-of-test message
                if data.startswith(b"{"):

                    try:
                        message = json.loads(data.decode())

                        if message.get("type") == "END":
                            finish_run(message, address)

                    except (ValueError, UnicodeDecodeError):
                        print("Invalid control message")

                    continue

                if len(data) < HEADER.size:
                    continue

                try:
                    magic, run_bytes, sequence, timestamp = (
                        HEADER.unpack(data[:HEADER.size])
                    )

                except struct.error:
                    continue

                if magic != MAGIC:
                    continue

                run_id = run_bytes.hex()

                stats = runs.setdefault(run_id, {
                    "sequences": set(),
                    "bytes": 0,
                    "duplicates": 0,
                    "out_of_order": 0,
                    "last_sequence": 0
                })

                if sequence in stats["sequences"]:

                    stats["duplicates"] += 1

                else:

                    if sequence < stats["last_sequence"]:
                        stats["out_of_order"] += 1

                    stats["sequences"].add(sequence)
                    stats["bytes"] += len(data)

                    stats["last_sequence"] = max(
                        stats["last_sequence"], sequence
                    )

                print(
                    f"Received Packet | "
                    f"Run: {run_id[:8]} | "
                    f"Sequence: {sequence} | "
                    f"Size: {len(data)} bytes"
                )

        except KeyboardInterrupt:

            print("\nReceiver stopped.")
            print("Completed test runs:", len(completed_runs))


if __name__ == "__main__":
    main()
