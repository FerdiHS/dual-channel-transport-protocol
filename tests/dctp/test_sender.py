"""
A module to unit test the Sender class in dctp.sender.
"""

import time

from dctp.packet import Packet
from dctp.sender import Sender
from dctp.types import PacketType, SackBlock


def fake_clock() -> int:
    """
    Monotonic clock in milliseconds for testing purposes.

    Returns:
        int: The current monotonic time in milliseconds.
    """
    return int(time.monotonic() * 1000)


def test_offer_and_first_send() -> None:
    """
    Test that offering data to the sender works and packets are created correctly.

    Returns:
        None
    """
    s = Sender(mss=100, window=300, now_ms=fake_clock)
    accepted = s.offer(b"A" * 250)
    assert accepted == 250
    out = s.due_packets()

    sizes = [len(p.payload) for p in out]
    assert sizes == [100, 100, 50]
    assert all(p.typ == PacketType.DATA for p in out)


def test_ack_marks_acked_and_slides_window() -> None:
    """

    Test that receiving an ACK marks segments as acknowledged and allows new data to be offered.

    Returns:
        None
    """
    s = Sender(mss=100, window=200, now_ms=fake_clock)
    s.offer(b"A" * 200)
    _ = s.due_packets()

    a = Packet(
        typ=PacketType.ACK,
        channel_type=0,
        seq=0,
        ts_send=0,
        ack=100,
        rcv_wnd=0,
        ts_echo=0,
        payload=b"",
    )
    s.on_feedback(a)

    accepted = s.offer(b"B" * 100)
    assert accepted == 100


def test_sack_marks_higher_ranges() -> None:
    """
    Test that receiving a SACK marks the specified segments as acknowledged.

    Returns:
        None
    """
    s = Sender(mss=100, window=400, now_ms=fake_clock)
    s.offer(b"A" * 400)
    _ = s.due_packets()

    sa = Packet(
        typ=PacketType.SACK,
        channel_type=0,
        seq=0,
        ts_send=0,
        ack=0,
        rcv_wnd=0,
        ts_echo=0,
        sack=[(200, 300), (300, 400)],
        payload=b"",
    )

    sa.sack = [SackBlock(*x) for x in sa.sack]
    s.on_feedback(sa)

    for seg in s.inflight.values():
        if seg.seq >= 200:
            assert seg.acked
        else:
            assert not seg.acked
