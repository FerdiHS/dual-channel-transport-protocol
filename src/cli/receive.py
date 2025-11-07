"""
A command-line utility to receive a file using DCTP over UDP.

Usage:
    poetry run dctp-recv --listen 127.0.0.1:9001 --out out.bin -v --sack
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Tuple

from dctp.transport import Transport


def _addr(s: str) -> Tuple[str, int]:
    """
    Parse a HOST:PORT address string into a (host, port) tuple.

    Args:
        s: The address string in the format HOST:PORT.

    Returns:
        A tuple containing the host and port.
    """
    if ":" not in s:
        raise argparse.ArgumentTypeError("address must be HOST:PORT")
    host, port = s.rsplit(":", 1)
    try:
        p = int(port)
    except ValueError as e:
        raise argparse.ArgumentTypeError("PORT must be an integer") from e
    return host, p


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for DCTP receiver.

    Returns:
        An argparse.ArgumentParser instance configured for DCTP receiver.
    """
    p = argparse.ArgumentParser(description="Receive DCTP over UDP.")
    p.add_argument("--listen", type=_addr, required=True, help="HOST:PORT to bind")
    p.add_argument("--out", type=str, required=True, help="output file path")
    p.add_argument(
        "--buf-cap",
        type=int,
        default=64 * 1024 - 1,
        help="receive buffer/window (bytes)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--sack", dest="sack", action="store_true", help="enable SACK (default)"
    )
    g.add_argument("--no-sack", dest="sack", action="store_false", help="disable SACK")
    p.set_defaults(sack=True)
    return p


def main(argv: list[str] | None = None) -> int:
    """
    Main function for the DCTP receiver command-line utility.

    Args:
        argv: List of command-line arguments. If None, uses sys.argv.

    Returns:
        An integer exit code.
    """
    args = build_parser().parse_args(argv)

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    t = Transport(
        window=args.buf_cap,
        prob_reliable=1.0,
        sack_enabled=args.sack,
        verbose=args.verbose,
    )
    t.bind(args.listen)

    total = 0
    started = time.time()

    try:
        with open(out_path, "wb") as f:
            while True:
                t.poll(25)

                chunk = t.recv(1 << 20)
                if chunk:
                    f.write(chunk)
                    total += len(chunk)

    except KeyboardInterrupt:
        if args.verbose:
            print("\n[dctp-recv] interrupted; closing…", file=sys.stderr)
    finally:
        t.close()

    elapsed = max(time.time() - started, 1e-6)
    mbps = (total * 8) / (elapsed * 1_000_000)
    print(f"[dctp-recv] received {total} bytes in {elapsed:.3f}s  |  {mbps:.2f} Mb/s")

    try:
        stats = t.get_stats()
        print("[dctp-recv] stats:", {k: stats[k] for k in sorted(stats)})
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
