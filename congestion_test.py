"""Run inside the Mininet CLI with: h1 python3 congestion_test.py --rate-mbps 8 --duration 30

UDP test traffic for the 5 Mbps primary bottleneck. Start the team's UDP receiver
on h2 before running this script. The controller must already have working rules.
"""
import argparse
import socket
import time


def main():
    parser = argparse.ArgumentParser(description='Generate rate-controlled UDP traffic')
    parser.add_argument('--destination', default='10.0.0.2')
    parser.add_argument('--port', type=int, default=5005)
    parser.add_argument('--rate-mbps', type=float, default=8.0)
    parser.add_argument('--duration', type=float, default=30.0)
    parser.add_argument('--packet-size', type=int, default=1200)
    args = parser.parse_args()
    if args.rate_mbps <= 0 or args.duration <= 0:
        parser.error('rate-mbps and duration must be positive')
    if not 64 <= args.packet_size <= 65000:
        parser.error('packet-size must be between 64 and 65000 bytes')
    interval = args.packet_size * 8 / (args.rate_mbps * 1_000_000)
    packet = b'C' * args.packet_size
    count = 0
    start = time.monotonic()
    next_send = start
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while time.monotonic() - start < args.duration:
            sock.sendto(packet, (args.destination, args.port))
            count += 1
            next_send += interval
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    elapsed = time.monotonic() - start
    offered_mbps = count * args.packet_size * 8 / elapsed / 1_000_000
    print('Packets sent:', count)
    print('Elapsed seconds: {:.2f}'.format(elapsed))
    print('Approximate application offered load: {:.2f} Mbps'.format(offered_mbps))
    print('Receiver measurements are needed to determine delivered throughput/loss')


if __name__ == '__main__':
    main()
