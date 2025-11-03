"""
A module to unit test the Packet class in dctp.packet.
"""

import os
import struct

import pytest

from dctp.packet import ACK_LEN, BASE_FMT, BASE_LEN, MAX_PAYLOAD, SACK_HDR_LEN, Packet
from dctp.types import ChannelType, PacketType, SackBlock


def test_data_roundtrip_various_sizes():
    """Test roundtrip serialization/deserialization of DATA packets with various payload sizes."""
    for size in (0, 1, 7, 128, 1024, MAX_PAYLOAD):
        p = Packet(
            typ=PacketType.DATA,
            channel_type=ChannelType.UNRELIABLE,
            seq=123,
            ts_send=456,
            payload=os.urandom(size),
        )
        raw = p.to_bytes()
        q = Packet.from_bytes(raw)
        assert q == p
        assert len(raw) == BASE_LEN + size


def test_ack_roundtrip_and_len_zero():
    """Test roundtrip serialization/deserialization of ACK packets and verify length is correct."""
    p = Packet(
        typ=PacketType.ACK,
        channel_type=ChannelType.UNRELIABLE,
        seq=1000,
        ts_send=111,
        ack=2000,
        rcv_wnd=4096,
        ts_echo=222,
        payload=b"",
    )
    raw = p.to_bytes()
    q = Packet.from_bytes(raw)
    assert q == p
    assert len(raw) == BASE_LEN + ACK_LEN


def test_sack_roundtrip_multiple_blocks():
    """Test roundtrip serialization/deserialization of SACK packets with multiple SACK blocks."""
    blocks = [SackBlock(3000, 4000), SackBlock(4500, 5000)]
    p = Packet(
        typ=PacketType.SACK,
        channel_type=ChannelType.UNRELIABLE,
        seq=1000,
        ts_send=333,
        ack=2000,
        rcv_wnd=2048,
        ts_echo=444,
        sack=blocks,
    )
    raw = p.to_bytes()
    q = Packet.from_bytes(raw)
    assert q == p
    expected = BASE_LEN + ACK_LEN + SACK_HDR_LEN + 8 * len(blocks)
    assert len(raw) == expected


def test_reject_control_with_payload():
    """Test that CTRL packets with non-empty payloads are rejected."""
    p = Packet(
        typ=PacketType.ACK,
        channel_type=ChannelType.UNRELIABLE,
        seq=1,
        ts_send=1,
        ack=2,
        rcv_wnd=1,
        ts_echo=1,
        payload=b"x",
    )
    with pytest.raises(ValueError, match="must have len == 0"):
        _ = p.to_bytes()


def test_reject_oversize_payload():
    """Test that packets with payloads exceeding MAX_PAYLOAD are rejected."""
    p = Packet(
        typ=PacketType.DATA,
        channel_type=ChannelType.UNRELIABLE,
        seq=0,
        ts_send=0,
        payload=b"\x00" * (MAX_PAYLOAD + 1),
    )
    with pytest.raises(ValueError, match="payload too large"):
        _ = p.to_bytes()


def test_checksum_catches_corruption():
    """Test that checksum verification detects corrupted packets."""
    p = Packet(typ=PacketType.DATA, channel_type=0, seq=7, ts_send=9, payload=b"abcdef")
    raw = bytearray(p.to_bytes())
    raw[-1] ^= 0x01
    with pytest.raises(ValueError, match="checksum mismatch"):
        Packet.from_bytes(bytes(raw))


def test_length_mismatch_detected():
    """Test that length mismatch in header is detected."""
    p = Packet(typ=PacketType.DATA, channel_type=0, seq=1, ts_send=1, payload=b"xyz")
    raw = bytearray(p.to_bytes())

    typ_u8, flags, seq, ts_send, length, ck = struct.unpack(BASE_FMT, raw[:BASE_LEN])
    bad_base = struct.pack(BASE_FMT, typ_u8, flags, seq, ts_send, length + 1, ck)
    tampered = bytes(bad_base) + bytes(raw[BASE_LEN:])
    with pytest.raises(ValueError, match="length mismatch"):
        Packet.from_bytes(tampered)
