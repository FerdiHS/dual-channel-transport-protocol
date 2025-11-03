"""
A module defining types and constants for the DCTP (Dual Channel Transport Protocol).

Classes:
    PacketType: Enumeration of DCTP packet types.
    Flag: Enumeration of bit flags used in DCTP packets.
    SackBlock: Named tuple representing a SACK block as a byte range.
"""

from __future__ import annotations

from enum import IntEnum
from typing import NamedTuple


class PacketType(IntEnum):
    """Frame type carried in the first byte of the base header."""

    DATA = 1  # Data segment
    ACK = 2  # Cumulative ACK + flow-control
    SACK = 3  # ACK + Selective ACK blocks
    CTRL = 4  # Reserved for future control messages


class ChannelType(IntEnum):
    """Channel type."""

    UNRELIABLE = 0  # Unreliable channel
    RELIABLE = 1  # Reliable channel


class SackBlock(NamedTuple):
    """
    One SACK block as a half-open byte range [start, end).

    Use byte sequence numbers for generality. `start < end` must hold.
    """

    start: int
    end: int
