
import subprocess
import sys

# Receiver computer IP address
RECEIVER_IP = "127.0.0.1"

PORT = 5005

# LOW, MODERATE AND HIGH TRAFFIC
TESTS = [
    ("LOW", 5, 256, 10),
    ("MODERATE", 50, 512, 10),
    ("HIGH", 200, 1024, 10)
]


def main():

    print("\n========== D2 UDP TRAFFIC TEST ==========")

    print("Receiver:", RECEIVER_IP)
    print("Port:", PORT)

    print("\nMake sure udp_receiver.py is running.")

    for label, rate, size, duration in TESTS:

        print("\n===================================")
        print(label, "TRAFFIC TEST")
        print("===================================")

        print("Rate:", rate, "packets/sec")
        print("Packet size:", size, "bytes")
        print("Duration:", duration, "seconds")

        command = [
            sys.executable,
            "udp_sender.py",
            "--host", RECEIVER_IP,
            "--port", str(PORT),
            "--rate", str(rate),
            "--size", str(size),
            "--duration", str(duration)
        ]

        result = subprocess.run(command)

        if result.returncode != 0:
            print(label, "test failed")

        else:
            print(label, "test completed successfully")

    print("\n========== ALL TESTS COMPLETED ==========")

    print("Check sender_stats.csv")
    print("Check receiver_stats.csv")


if __name__ == "__main__":
    main()
