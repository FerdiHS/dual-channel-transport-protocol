"""
A module defining the Packet data model for DCTP (Dual Channel Transport Protocol).

Classes:
    Packet: A class representing a DCTP frame in a unified way.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import ClassVar, List

from .types import ChannelType, PacketType, SackBlock

BASE_FMT = "!BBIIHH"  # type, channel_type, seq, ts_send, len, checksum
BASE_LEN = struct.calcsize(BASE_FMT)

ACK_FMT = "!IHI"  # ack, rcv_wnd, ts_echo
ACK_LEN = struct.calcsize(ACK_FMT)

SACK_HDR_FMT = "!BB"  # block_cnt, reserved
SACK_HDR_LEN = struct.calcsize(SACK_HDR_FMT)

MAX_PAYLOAD = 1400
MAX_SACK_BLOCKS = 32


@dataclass
class Packet:
    """
    A class representing a DCTP frame in a unified way.

    Attributes:
        typ (PacketType): The type of the packet (DATA, ACK, SACK, CTRL).
        channel_type (ChannelType): Whether the channel is reliable.
        seq (int): Sequence number of the packet.
        ts_send (int): Timestamp when the packet was sent.
        payload (bytes): Payload of the packet (empty for control frames).
        ack (int): Cumulative ACK number (for ACK/SACK packets).
        rcv_wnd (int): Receive window size (for ACK/SACK packets).
        ts_echo (int): Echoed timestamp (for ACK/SACK packets).
        sack (List[SackBlock]): List of SACK blocks (for SACK packets).

    Methods:
        to_bytes() -> bytes: Serialize the Packet into a wire frame.
        from_bytes(frame: bytes) -> Packet: Parse a wire frame into a Packet.
    """

    typ: PacketType
    channel_type: ChannelType

    seq: int
    ts_send: int
    payload: bytes = b""

    ack: int = 0
    rcv_wnd: int = 0
    ts_echo: int = 0
    sack: List[SackBlock] = field(default_factory=list)

    BASE_LEN: ClassVar[int] = BASE_LEN
    ACK_LEN: ClassVar[int] = ACK_LEN
    MAX_PAYLOAD: ClassVar[int] = MAX_PAYLOAD
    MAX_SACK_BLOCKS: ClassVar[int] = MAX_SACK_BLOCKS

    def to_bytes(self) -> bytes:
        """
        Serialize this Packet into a wire frame.

        Returns:
            bytes: The serialized wire frame.

        Raises:
            ValueError: on invalid field ranges, illegal combinations, or overflow.
        """
        _u8("type", int(self.typ))
        _u8("channel_type", int(self.channel_type))
        _u32("seq", self.seq)
        _u32("ts_send", self.ts_send)

        extras = b""

        if self.typ == PacketType.DATA:
            length = len(self.payload)
            if length > MAX_PAYLOAD:
                raise ValueError(f"payload too large: {length} > {MAX_PAYLOAD}")
            _u16("len", length)

        elif self.typ == PacketType.ACK:
            _ensure_no_payload(self.payload)
            _u32("ack", self.ack)
            _u16("rcv_wnd", self.rcv_wnd)
            _u32("ts_echo", self.ts_echo)
            extras = struct.pack(ACK_FMT, self.ack, self.rcv_wnd, self.ts_echo)
            length = 0

        elif self.typ == PacketType.SACK:
            _ensure_no_payload(self.payload)
            _u32("ack", self.ack)
            _u16("rcv_wnd", self.rcv_wnd)
            _u32("ts_echo", self.ts_echo)

            if len(self.sack) > MAX_SACK_BLOCKS:
                raise ValueError(f"too many SACK blocks: {len(self.sack)} > {MAX_SACK_BLOCKS}")

            for i, (start, end) in enumerate(self.sack):
                _u32(f"sack[{i}].start", start)
                _u32(f"sack[{i}].end", end)
                if not (start < end):
                    raise ValueError(f"sack[{i}] invalid range: [{start}, {end})")

            extras = struct.pack(ACK_FMT, self.ack, self.rcv_wnd, self.ts_echo)
            extras += struct.pack(SACK_HDR_FMT, len(self.sack), 0)
            for blk in self.sack:
                extras += struct.pack("!II", blk.start, blk.end)
            length = 0

        else:
            _ensure_no_payload(self.payload)
            length = 0

        base_wo_ck = struct.pack(
            BASE_FMT,
            int(self.typ),
            self.channel_type,
            self.seq,
            self.ts_send,
            length,
            0,
        )
        ck = _checksum(base_wo_ck + extras + self.payload)
        base = base_wo_ck[:-2] + struct.pack("!H", ck)
        return base + extras + self.payload

    @staticmethod
    def from_bytes(frame: bytes) -> "Packet":
        """
        Parse a wire frame into a Packet and validate checksum/lengths.

        Args:
            frame (bytes): The wire frame to parse.

        Returns:
            Packet: The parsed Packet object.

        Raises:
            ValueError: if the frame is malformed or checksum fails.
        """
        if len(frame) < BASE_LEN:
            raise ValueError(f"frame too short: {len(frame)} < {BASE_LEN}")

        typ_u8, channel_type_int, seq, ts_send, length, ck = struct.unpack(
            BASE_FMT, frame[:BASE_LEN]
        )
        try:
            typ = PacketType(typ_u8)
        except ValueError as e:
            raise ValueError(f"unknown packet type: {typ_u8}") from e

        offs = BASE_LEN
        ack = 0
        rcv_wnd = 0
        ts_echo = 0
        sack_blocks: List[SackBlock] = []

        extras_len = 0
        if typ == PacketType.DATA:
            extras = b""

        elif typ == PacketType.ACK:
            _require_at_least(frame, offs, ACK_LEN, "ACK section")
            ack, rcv_wnd, ts_echo = struct.unpack(ACK_FMT, frame[offs : offs + ACK_LEN])
            extras = frame[offs : offs + ACK_LEN]
            offs += ACK_LEN
            extras_len += ACK_LEN
            if length != 0:
                raise ValueError("ACK frame must have len == 0")

        elif typ == PacketType.SACK:
            _require_at_least(frame, offs, ACK_LEN, "ACK section")
            ack, rcv_wnd, ts_echo = struct.unpack(ACK_FMT, frame[offs : offs + ACK_LEN])
            extras = frame[offs : offs + ACK_LEN]
            offs += ACK_LEN
            extras_len += ACK_LEN
            if length != 0:
                raise ValueError("SACK frame must have len == 0")

            _require_at_least(frame, offs, SACK_HDR_LEN, "SACK header")
            block_cnt, reserved = struct.unpack(SACK_HDR_FMT, frame[offs : offs + SACK_HDR_LEN])
            if reserved != 0:
                raise ValueError("SACK reserved byte must be 0")
            extras += frame[offs : offs + SACK_HDR_LEN]
            offs += SACK_HDR_LEN
            extras_len += SACK_HDR_LEN

            if block_cnt > MAX_SACK_BLOCKS:
                raise ValueError(f"SACK block_cnt too large: {block_cnt} > {MAX_SACK_BLOCKS}")

            need = block_cnt * 8
            _require_at_least(frame, offs, need, "SACK blocks")
            for i in range(block_cnt):
                start, end = struct.unpack("!II", frame[offs + 8 * i : offs + 8 * (i + 1)])
                if not (start < end):
                    raise ValueError(f"SACK block {i} invalid range: [{start}, {end})")
                sack_blocks.append(SackBlock(start, end))
            extras += frame[offs : offs + need]
            offs += need
            extras_len += need

        else:
            extras = b""
            if length != 0:
                raise ValueError("CTRL frame must have len == 0")

        expected_total = BASE_LEN + extras_len + length
        if len(frame) != expected_total:
            raise ValueError(
                f"length mismatch: header len={length}, extras={extras_len}, "
                f"total expected={expected_total}, actual={len(frame)}"
            )

        payload = frame[-length:] if length else b""

        base_wo_ck = struct.pack(BASE_FMT, typ_u8, channel_type_int, seq, ts_send, length, 0)
        expected_ck = _checksum(base_wo_ck + extras + payload)
        if ck != expected_ck:
            raise ValueError("checksum mismatch")

        return Packet(
            typ=typ,
            channel_type=ChannelType(channel_type_int),
            seq=seq,
            ts_send=ts_send,
            payload=payload,
            ack=ack,
            rcv_wnd=rcv_wnd,
            ts_echo=ts_echo,
            sack=sack_blocks,
        )


def _checksum(b: bytes) -> int:
    """
    Compute 16-bit internet checksum (one's complement sum over 16-bit words).

    If `b` has odd length, a trailing zero byte is added for summation only.

    Args:
        b (bytes): The byte sequence to checksum.

    Returns:
        int: The computed checksum as a 16-bit integer.
    """
    total = 0
    if len(b) % 2 == 1:
        b += b"\x00"
    for i in range(0, len(b), 2):
        total += (b[i] << 8) | (b[i + 1])
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _ensure_no_payload(payload: bytes) -> None:
    """
    Ensure that control frames have no payload.

    Args:
        payload (bytes): The payload to check.

    Returns:
        None

    Raises:
        ValueError: if the payload is not empty.
    """
    if payload:
        raise ValueError("control frames (ACK/SACK/CTRL) must have len == 0 payload")


def _require_at_least(buf: bytes, start: int, need: int, what: str) -> None:
    """
    Ensure that buf[start:] has at least `need` bytes.

    Args:
        buf (bytes): The buffer to check.
        start (int): The starting index.
        need (int): The number of bytes needed.
        what (str): Description of the section being checked.

    Returns:
        None

    Raises:
        ValueError: if there are not enough bytes.
    """
    if len(buf) - start < need:
        raise ValueError(f"truncated {what}: need {need}, have {len(buf) - start}")


def _u8(name: str, v: int) -> None:
    """
    Ensure that `v` fits in an unsigned 8-bit integer.

    Args:
        name (str): The name of the variable.
        v (int): The value to check.

    Returns:
        None

    Raises:
        ValueError: if `v` is out of range.
    """
    if not (0 <= v <= 0xFF):
        raise ValueError(f"{name} out of range for u8: {v}")


def _u16(name: str, v: int) -> None:
    """
    Ensure that `v` fits in an unsigned 16-bit integer.

    Args:
        name (str): The name of the variable.
        v (int): The value to check.

    Returns:
        None

    Raises:
        ValueError: if `v` is out of range.
    """
    if not (0 <= v <= 0xFFFF):
        raise ValueError(f"{name} out of range for u16: {v}")


def _u32(name: str, v: int) -> None:
    """
    Ensure that `v` fits in an unsigned 32-bit integer.

    Args:
        name (str): The name of the variable.
        v (int): The value to check.

    Returns:
        None

    Raises:
        ValueError: if `v` is out of range.
    """
    if not (0 <= v <= 0xFFFFFFFF):
        raise ValueError(f"{name} out of range for u32: {v}")
