"""
A command-line utility to send data using DCTP over UDP.

Usage:
    poetry run dctp-send --dst 127.0.0.1:9001 --num-packets 50 --rate 5 --prob-reliable 1.0 -v --sack
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Tuple

from dctp.transport import Transport


def _addr(s: str) -> Tuple[str, int]:
    """Parse HOST:PORT into a (host, port) tuple."""
    if ":" not in s:
        raise argparse.ArgumentTypeError("address must be HOST:PORT")
    host, port = s.rsplit(":", 1)
    try:
        p = int(port)
    except ValueError as e:
        raise argparse.ArgumentTypeError("PORT must be an integer") from e
    return host, p


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for DCTP sender."""
    p = argparse.ArgumentParser(description="Send data using DCTP over UDP.")
    p.add_argument("--dst", type=_addr, required=True, help="destination HOST:PORT")

    # Send multiple packets
    p.add_argument("--num-packets", type=int, help="number of packets to send (no file mode)")
    p.add_argument("--rate", type=float, help="packets per second (no file mode)")

    # Transmission configuration
    p.add_argument("--win", type=int, default=64 * 1024, help="sender window (bytes)")
    p.add_argument("--chunk", type=int, default=64 * 1024, help="read chunk size (bytes)")
    p.add_argument(
        "--prob-reliable",
        type=float,
        default=0.5,
        help="probability ∈ [0,1] that a segment is sent RELIABLE (default 1.0)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--sack", dest="sack", action="store_true", help="enable SACK (default)")
    g.add_argument("--no-sack", dest="sack", action="store_false", help="disable SACK")
    p.set_defaults(sack=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    prob_rel = max(0.0, min(1.0, float(args.prob_reliable)))
    t = Transport(
        window=args.win, prob_reliable=prob_rel, sack_enabled=args.sack, verbose=args.verbose
    )
    t.connect(args.dst)

    if args.num_packets and args.rate:
        total_queued = 0
        num_packets = args.num_packets
        rate = args.rate
        interval = 1.0 / rate
        started = time.time()

        print(f"[dctp-send] Sending {num_packets} packets at {rate} packets/sec")

        try:
            for i in range(num_packets):
                data = f"Packet {i+1}".encode()
                accepted = t.send(data)
                if accepted <= 0:
                    t.poll(10)
                    continue
                t.poll(0)
                if args.verbose:
                    print(f"[dctp-send] Sent packet {i+1}/{num_packets}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[dctp-send] interrupted; closing…", file=sys.stderr)
        finally:
            t.drain()
            t.close()

        elapsed = max(time.time() - started, 1e-6)
        print(f"[dctp-send] Finished sending {num_packets} packets in {elapsed:.2f}s")

        try:
            elapsed = max(time.time() - started, 1e-6)
            mbps = (total_queued * 8) / (elapsed * 1_000_000)
            print(f"[dctp-send] queued {total_queued} bytes in {elapsed:.3f}s  |  {mbps:.2f} Mb/s")

            stats = t.get_stats()
            link_keys = (
                "bytes_tx",
                "bytes_rx",
                "frames_tx",
                "frames_rx",
                "acks_tx",
                "acks_rx",
                "sacks_tx",
                "sacks_rx",
            )
            print("[dctp-send] link:", {k: stats[k] for k in link_keys if k in stats})

            # Sender RTT/RTO metrics (pretty JSON)
            sender = stats.get("sender", {})
            if sender:
                print("[dctp-send] sender metrics:", end=" ")

                parts = []
                for k, v in sender.items():
                    if k == "rtt_samples_ms_last":
                        continue
                    parts.append(f"{k}={v}")
                print(", ".join(parts))


        except Exception:
            pass
        return 0

    else:
        print("[dctp-send] Error: must specify --num-packets and --rate", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
