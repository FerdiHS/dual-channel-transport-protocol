"""
A module to unit test the Receiver class in dctp.receiver.
"""

from dctp.types import PacketType, ChannelType
from dctp.packet import Packet
from dctp.receiver import Receiver


def mk_data(seq: int, s: bytes, channel_type: ChannelType = ChannelType.RELIABLE) -> Packet:
    return Packet(typ=PacketType.DATA, channel_type=channel_type, seq=seq, ts_send=111, payload=s)

def test_unreliable_channel_data_is_ignored():
    r = Receiver(rcv_nxt=0)
    a1 = r.on_data(mk_data(0, b"ABC", channel_type=ChannelType.UNRELIABLE))
    assert a1 is None
    assert r.pop_deliverable() == b"ABC"

def test_in_order_delivery_and_ack():
    r = Receiver(rcv_nxt=1000)
    a1 = r.on_data(mk_data(1000, b"abc"))
    assert a1.ack == 1003
    assert r.pop_deliverable() == b"abc"


def test_out_of_order_then_fill_the_gap():
    r = Receiver(rcv_nxt=0)
    a1 = r.on_data(mk_data(3, b"DEF"))
    assert a1.ack == 0
    assert r.pop_deliverable() == b""

    a2 = r.on_data(mk_data(0, b"ABC"))
    assert a2.ack == 6
    assert r.pop_deliverable() == b"ABCDEF"


def test_duplicate_below_rcvnxt_is_ignored():
    r = Receiver(rcv_nxt=0)
    r.on_data(mk_data(0, b"AAA"))
    r.on_data(mk_data(3, b"BBB"))
    assert r.pop_deliverable() == b"AAABBB"
    ack = r.on_data(mk_data(0, b"AAA"))
    assert ack.ack == 6
    assert r.pop_deliverable() == b""
