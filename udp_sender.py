
import argparse
import csv
import json
import socket
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Packet header: magic + run ID + sequence number + timestamp
HEADER = struct.Struct("!4s16sId")
MAGIC = b"UDPD"
MAX_UDP_PAYLOAD = 65507


def save_stats(filename, row):
    file_exists = Path(filename).exists()

    with open(filename, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())

        if not file_exists or Path(filename).stat().st_size == 0:
            writer.writeheader()

        writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="UDP Traffic Sender")

    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--rate", type=float, default=10)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--stats", default="sender_stats.csv")

    args = parser.parse_args()

    if args.rate <= 0 or args.duration <= 0:
        parser.error("Rate and duration must be positive")

    if not HEADER.size <= args.size <= MAX_UDP_PAYLOAD:
        parser.error("Invalid packet size")

    run_id = uuid.uuid4()
    destination = (args.host, args.port)

    interval = 1.0 / args.rate
    payload = b"X" * (args.size - HEADER.size)

    sent_packets = 0
    sent_bytes = 0
    send_errors = 0

    start = time.perf_counter()
    end_time = start + args.duration
    next_send = start

    print("\n========== UDP SENDER ==========")
    print(f"Receiver: {args.host}:{args.port}")
    print(f"Rate: {args.rate} packets/sec")
    print(f"Packet size: {args.size} bytes")
    print(f"Duration: {args.duration} seconds")
    print(f"Run ID: {run_id.hex}")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:

        while next_send < end_time:

            now = time.perf_counter()

            if now < next_send:
                time.sleep(next_send - now)

            if time.perf_counter() >= end_time:
                break

            sequence = sent_packets + 1
            timestamp = time.time()

            packet = (
                HEADER.pack(
                    MAGIC,
                    run_id.bytes,
                    sequence,
                    timestamp
                ) + payload
            )

            try:
                sock.sendto(packet, destination)
                sent_packets += 1
                sent_bytes += len(packet)

            except OSError as error:
                send_errors += 1
                print("Send error:", error)

            next_send += interval

        elapsed = time.perf_counter() - start

        summary = {
            "type": "END",
            "run_id": run_id.hex,
            "sent_packets": sent_packets,
            "sent_bytes": sent_bytes,
            "send_errors": send_errors,
            "duration_seconds": elapsed,
            "rate_pps": args.rate,
            "packet_size_bytes": args.size
        }

        # Inform receiver that this test has ended.
        end_message = json.dumps(summary).encode()

        for _ in range(5):
            try:
                sock.sendto(end_message, destination)
            except OSError:
                pass

            time.sleep(0.1)

    throughput = (
        sent_bytes * 8 / elapsed / 1_000_000
        if elapsed else 0
    )

    row = {
        "run_id": run_id.hex,
        "destination": f"{args.host}:{args.port}",
        "sent_packets": sent_packets,
        "sent_bytes": sent_bytes,
        "send_errors": send_errors,
        "duration_seconds": round(elapsed, 4),
        "configured_rate_pps": args.rate,
        "packet_size_bytes": args.size,
        "sender_throughput_mbps": round(throughput, 6)
    }

    save_stats(args.stats, row)

    print("\n========== SENDER STATISTICS ==========")
    print("Packets sent:", sent_packets)
    print("Bytes sent:", sent_bytes)
    print("Send errors:", send_errors)
    print(f"Throughput: {throughput:.6f} Mbps")
    print("Statistics saved to:", args.stats)


if __name__ == "__main__":
    main()
