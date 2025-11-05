"""
A module that implements the Transport class for DCTP.

Classes:
    Transport: A class that combines Sender and Receiver for DCTP.
"""

import select
import socket
from typing import Tuple

from .packet import Packet
from .receiver import Receiver
from .sender import Sender
from .types import PacketType

DEFAULT_MTU = 1200
DEFAULT_WINDOW = 64 * 1024 - 1
DEFAULT_PROB_RELIABLE = 0.5


class Transport:
    """
    A class that implements transport in DCTP.

    Attributes:
        mtu (int): Maximum Transmission Unit.
        verbose (bool): Verbose logging flag.
        sack_enabled (bool): Whether SACK is enabled.
        sender (Sender): The sender instance.
        receiver (Receiver): The receiver instance.

    Methods:
        _on_inbound_frame(raw: bytes, src) -> None:
            Process an inbound frame.
        _flush_due() -> int:
            Flush due packets from the sender.
    """

    def __init__(
        self,
        mtu: int = DEFAULT_MTU,
        window: int = DEFAULT_WINDOW,
        prob_reliable: float = DEFAULT_PROB_RELIABLE,
        sack_enabled: bool = True,
        verbose: bool = False,
    ):
        self.mtu = int(mtu)
        self.verbose = bool(verbose)
        self.sack_enabled = bool(sack_enabled)
        self.sender = Sender(
            mss=self.mtu - Packet.BASE_LEN,
            window=window,
            prob_reliable=prob_reliable,
            sack_enabled=sack_enabled,
            verbose=self.verbose,
        )
        self.receiver = Receiver(
            wnd_bytes=window, sack_enabled=self.sack_enabled, verbose=self.verbose
        )

        self._sock = None
        self._peer = None

        self._bytes_tx = 0
        self._bytes_rx = 0
        self._frames_tx = 0
        self._frames_rx = 0
        self._acks_tx = 0
        self._acks_rx = 0
        self._sacks_tx = 0
        self._sacks_rx = 0

    def bind(self, addr: Tuple[str, int]) -> None:
        """
        Bind the transport to a local address.

        Args:
            addr (Tuple[str, int]): The local address to bind to.

        Returns:
            None
        """
        self._ensure_socket()
        assert self._sock is not None
        self._sock.bind(addr)
        if self.verbose:
            print(f"[dctp] bind on {addr}")

    def connect(self, addr: Tuple[str, int]) -> None:
        """
        Connect the transport to a remote address.

        Args:
            addr (Tuple[str, int]): The remote address to connect to.

        Returns:
            None
        """
        self._ensure_socket()
        self._peer = addr
        if self.verbose:
            print(f"[dctp] connect → {addr}")

    def send(self, data: bytes) -> int:
        """
        Send data through the transport.

        Args:
            data (bytes): The data to send.

        Returns:
            int: The number of bytes sent.
        """
        n = self.sender.offer(data)
        self._flush_due()
        return n

    def recv(self, max_bytes: int) -> bytes:
        """
        Receive data from the transport.

        Args:
            max_bytes (int): The maximum number of bytes to receive.

        Returns:
            bytes: The received data.
        """
        return self.receiver.pop_deliverable()[:max_bytes]

    def poll(self, timeout_ms: int) -> None:
        """
        Poll the transport for incoming data and flush due packets.

        Args:
            timeout_ms (int): The timeout in milliseconds.

        Returns:
            None
        """
        self._flush_due()
        if not self._sock:
            return
        r, _, _ = select.select([self._sock], [], [], max(timeout_ms, 0) / 1000.0)
        if not r:
            return
        while True:
            try:
                raw, src = self._sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                break
            self._bytes_rx += len(raw)
            self._frames_rx += 1
            self._on_inbound(raw, src)
        self._flush_due()

    def drain(self) -> None:
        """
        Drain the transport by flushing all due packets.

        Returns:
            None
        """
        while self.sender.get_inflight_segments():
            self._flush_due()
            self.poll(5)

    def close(self) -> None:
        """
        Close the transport and release resources.

        Returns:
            None
        """
        if self._sock:
            self._sock.close()
            self._sock = None

    def get_stats(self) -> dict:
        base = {
            "bytes_tx": self._bytes_tx,
            "bytes_rx": self._bytes_rx,
            "frames_tx": self._frames_tx,
            "frames_rx": self._frames_rx,
            "acks_tx": self._acks_tx,
            "acks_rx": self._acks_rx,
            "sacks_tx": self._sacks_tx,
            "sacks_rx": self._sacks_rx,
        }
        # Attach sender metrics if available
        try:
            base["sender"] = self.sender.metrics()
        except Exception:
            base["sender"] = {}
        return base

    def _on_inbound_frame(self, raw: bytes, src):
        """
        Process an inbound frame.

        Args:
            raw (bytes): The raw bytes of the inbound frame.
            src: The source address of the inbound frame.

        Returns:
            None
        """
        pkt = Packet.from_bytes(raw)

        if self._peer is None and pkt.typ == PacketType.DATA:
            self._peer = src
            if self.verbose:
                print(f"[dctp] learned peer = {src}")

        if pkt.typ == PacketType.DATA:
            fb = self.receiver.on_data(pkt)
            if fb is not None:
                self._send_pkt(fb, dst=src)
                if fb.typ == PacketType.ACK:
                    self._acks_tx += 1
                elif fb.typ == PacketType.SACK:
                    self._sacks_tx += 1
        elif pkt.typ in (PacketType.ACK, PacketType.SACK):
            self.sender.on_feedback(pkt)
            if pkt.typ == PacketType.ACK:
                self._acks_rx += 1
            else:
                self._sacks_rx += 1

    def _flush_due(self) -> int:
        """
        Flush due packets from the sender.

        Returns:
            int: The number of packets sent.
        """
        if not self._sock or not self._peer:
            return 0
        cnt = 0
        for pkt in self.sender.due_packets():
            self._send_pkt(pkt, dst=self._peer)
            cnt += 1
        return cnt

    def _ensure_socket(self) -> None:
        """
        Ensure that the UDP socket is created.

        Returns:
            None
        """
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
            if self.verbose:
                print(f"[dctp] created UDP socket")

    def _send_pkt(self, pkt: Packet, dst) -> None:
        """
        Send a packet to the specified destination.

        Args:
            pkt (Packet): The packet to send.
            dst: The destination address.

        Returns:
            None
        """
        assert self._sock is not None
        raw = pkt.to_bytes()
        self._sock.sendto(raw, dst)
        self._bytes_tx += len(raw)
        self._frames_tx += 1

    def _on_inbound(self, raw: bytes, src) -> None:
        """
        Process an inbound packet.

        Args:
            raw (bytes): The raw bytes of the inbound packet.
            src: The source address of the inbound packet.

        Returns:
            None
        """
        pkt = Packet.from_bytes(raw)

        if self._peer is None and pkt.typ == PacketType.DATA:
            self._peer = src
            if self.verbose:
                print(f"[dctp] learned peer = {src}")

        if pkt.typ == PacketType.DATA:
            fb = self.receiver.on_data(pkt)
            if fb is not None:
                self._send_pkt(fb, dst=src)
                if fb.typ == PacketType.ACK:
                    self._acks_tx += 1
                elif fb.typ == PacketType.SACK:
                    self._sacks_tx += 1
        elif pkt.typ in (PacketType.ACK, PacketType.SACK):
            self.sender.on_feedback(pkt)
            if pkt.typ == PacketType.ACK:
                self._acks_rx += 1
            else:
                self._sacks_rx += 1
