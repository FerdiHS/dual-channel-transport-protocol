"""
A module implementing the Receiver for DCTP (Dual Channel Transport Protocol).

Classes:
    Receiver: A class that implements the receiver side of DCTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .packet import Packet
from .types import ChannelType, PacketType, SackBlock


@dataclass
class Receiver:
    """
    A class that implements the receiver side of DCTP.

    Attributes:
        rcv_nxt:   next in-order byte expected (cumulative ack point)
        wnd_bytes: advertised receive window (bytes)
        sack_enabled: whether SACK is enabled
    """

    rcv_nxt: int = 0
    wnd_bytes: int = 64 * 1024 - 1
    sack_enabled: bool = True
    verbose: bool = False
    _buf: Dict[int, bytes] = field(default_factory=dict)
    _delivered: bytearray = field(default_factory=bytearray)
    total_packets_received: int = 0  # total number of DATA packets received


    def on_data(self, pkt: Packet) -> Optional[Packet]:
        """
        Process an incoming DATA packet and return an ACK/SACK packet as feedback.

        Args:
            pkt (Packet): The incoming DATA packet.

        Returns:
            Optional[Packet]: An ACK or SACK packet to send back as feedback, or None for
            unreliable packets.
        """
        if pkt.typ != PacketType.DATA:
            raise ValueError("Receiver.on_data expects DATA packets")
        self.total_packets_received += 1
        self._print(
            f"Got DATA packet | seq={pkt.seq} | len={len(pkt.payload or b'')} | "
            f"ch={pkt.channel_type.name} | ts={pkt.ts_send} | msg={pkt.payload or b''}"
        )

        if pkt.channel_type == ChannelType.UNRELIABLE:
            if pkt.payload:
                self._delivered.extend(pkt.payload)
            return None

        seq = pkt.seq
        pay = pkt.payload or b""

        if seq > self.rcv_nxt:
            self._print(f"OUT-OF-ORDER: got [{seq},{seq+len(pay)}) expecting {self.rcv_nxt}")

        # Duplicate entirely before rcv_nxt
        if seq + len(pay) <= self.rcv_nxt:
            return self._feedback(ts_echo=pkt.ts_send)

        # Trim left overlap to unseen portion
        if seq < self.rcv_nxt:
            trim = self.rcv_nxt - seq
            if trim < len(pay):
                pay = pay[trim:]
                seq = self.rcv_nxt
            else:
                return self._feedback(ts_echo=pkt.ts_send)

        if pay:
            self._buf[seq] = pay

        self._consume_contiguous()

        return self._feedback(ts_echo=pkt.ts_send)

    def pop_deliverable(self) -> bytes:
        """
        Return app-deliverable bytes since last call (may be empty).

        Returns:
            bytes: Deliverable bytes.
        """
        if not self._delivered:
            return b""
        out = bytes(self._delivered)
        self._delivered.clear()
        return out

    def _consume_contiguous(self) -> None:
        """
        Greedily deliver any chunks that start exactly at rcv_nxt.

        Returns:
            None
        """
        while True:
            chunk = self._buf.pop(self.rcv_nxt, None)
            if chunk is None:
                return
            self._delivered.extend(chunk)
            self.rcv_nxt += len(chunk)

    def _feedback(self, ts_echo: int) -> Packet:
        """
        Build ACK or SACK depending on buffered gaps.
        channel_type for feedback is marked RELIABLE.

        Args:
            ts_echo (int): Timestamp to echo back.

        Returns:
            Packet: ACK or SACK packet.
        """
        blocks = self._build_sack_blocks(limit=4)
        if blocks and self.sack_enabled:
            return Packet(
                typ=PacketType.SACK,
                channel_type=ChannelType.RELIABLE,
                seq=self.rcv_nxt,
                ts_send=0,
                ack=self.rcv_nxt,
                rcv_wnd=self.wnd_bytes,
                ts_echo=ts_echo,
                sack=blocks,
                payload=b"",
            )
        return Packet(
            typ=PacketType.ACK,
            channel_type=ChannelType.RELIABLE,
            seq=self.rcv_nxt,
            ts_send=0,
            ack=self.rcv_nxt,
            rcv_wnd=self.wnd_bytes,
            ts_echo=ts_echo,
            payload=b"",
        )

    def _build_sack_blocks(self, limit: int) -> List[SackBlock]:
        """
        Build merged, non-overlapping SACK blocks for buffered data strictly above rcv_nxt.

        Args:
            limit (int): Maximum number of SACK blocks to return.

        Returns:
            List[SackBlock]: List of SACK blocks.
        """
        if not self.sack_enabled:
            return []
        spans: List[Tuple[int, int]] = []
        base = self.rcv_nxt
        for s, p in self._buf.items():
            e = s + len(p)
            if e <= base:
                continue
            s = max(s, base)
            if s < e:
                spans.append((s, e))
        if not spans:
            return []

        spans.sort()
        merged: List[SackBlock] = []
        cs, ce = spans[0]
        for s, e in spans[1:]:
            if s <= ce:
                ce = max(ce, e)
            else:
                merged.append(SackBlock(cs, ce))
                cs, ce = s, e
        merged.append(SackBlock(cs, ce))

        merged.sort(key=lambda b: b.start, reverse=True)

        cap = min(limit, Packet.MAX_SACK_BLOCKS)
        return merged[:cap]

    def _print(self, msg: str) -> None:
        """
        Print a verbose message if verbosity is enabled.

        Args:
            msg (str): The message to print.

        Returns:
            None
        """
        if self.verbose:
            print(f"[Receiver] {msg}")
