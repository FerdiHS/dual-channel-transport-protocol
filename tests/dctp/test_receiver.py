"""
A module to unit test the Receiver class in dctp.receiver.
"""

from dctp.packet import Packet
from dctp.receiver import Receiver
from dctp.types import ChannelType, PacketType


def mk_data(
    seq: int, s: bytes, channel_type: ChannelType = ChannelType.RELIABLE
) -> Packet:
    """
    Create a DATA packet for testing.

    Args:
        seq (int): The sequence number of the packet.
        s (bytes): The payload of the packet.
        channel_type (ChannelType): The channel type of the packet.

    Returns:
        Packet: The created DATA packet.
    """
    return Packet(
        typ=PacketType.DATA, channel_type=channel_type, seq=seq, ts_send=111, payload=s
    )


def test_unreliable_channel_data_is_ignored() -> None:
    """
    Test that data received on an unreliable channel is ignored.

    Returns:
        None
    """
    r = Receiver(rcv_nxt=0)
    a1 = r.on_data(mk_data(0, b"ABC", channel_type=ChannelType.UNRELIABLE))
    assert a1 is None
    assert r.pop_deliverable() == b"ABC"


def test_in_order_delivery_and_ack() -> None:
    """
    Test in-order data delivery and acknowledgment.

    Returns:
        None
    """
    r = Receiver(rcv_nxt=1000)
    a1 = r.on_data(mk_data(1000, b"abc"))
    assert a1.ack == 1003
    assert r.pop_deliverable() == b"abc"


def test_out_of_order_then_fill_the_gap() -> None:
    """
    Test out-of-order data reception followed by filling the gap.

    Returns:
        None
    """
    r = Receiver(rcv_nxt=0)
    a1 = r.on_data(mk_data(3, b"DEF"))
    assert a1.ack == 0
    assert r.pop_deliverable() == b""

    a2 = r.on_data(mk_data(0, b"ABC"))
    assert a2.ack == 6
    assert r.pop_deliverable() == b"ABCDEF"


def test_duplicate_below_rcvnxt_is_ignored() -> None:
    """

    Test that duplicate data below rcv_nxt is ignored.

    Returns:
        None
    """
    r = Receiver(rcv_nxt=0)
    r.on_data(mk_data(0, b"AAA"))
    r.on_data(mk_data(3, b"BBB"))
    assert r.pop_deliverable() == b"AAABBB"
    ack = r.on_data(mk_data(0, b"AAA"))
    assert ack.ack == 6
    assert r.pop_deliverable() == b""
