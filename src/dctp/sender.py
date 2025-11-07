"""
A module that implements the sender side of DCTP.

Classes:
    _Seg:   Segment metadata kept in the in-flight map.
    Sender: Selective-Repeat sender with per-segment routing (reliable/unreliable),
            RTO-based retransmission, Karn's rule RTT sampling, and basic RTT stats.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from utils.time import monotonic_ms

from .packet import Packet
from .types import ChannelType, PacketType

MAXIMUM_RTO_MS = 8000


@dataclass
class _Seg:
    """
    One data segment tracked by the sender.

    seq:        first byte offset of this segment in the stream
    end:        one-past-last byte offset
    payload:    the bytes to send
    chan:       ChannelType used for last transmission
    sent_ts:    last transmission monotonic timestamp (ms) or 0 if never sent
    acked:      True iff fully acknowledged (or retired for unreliable)
    retx_count: number of retransmissions already done
    rto_ms:     current RTO for this segment (ms)
    """

    seq: int
    end: int
    payload: bytes
    chan: ChannelType = ChannelType.RELIABLE
    sent_ts: int = 0
    acked: bool = False
    retx_count: int = 0
    rto_ms: int = 1000


class Sender:
    """
    A class that implements the sender side of DCTP.

    Attributes:
        mss:              Maximum segment size (bytes).
        win:              Sender's sliding window size (bytes).
        now_ms:          Callable returning current monotonic time in milliseconds.
        prob_reliable:    Probability of sending a segment over the reliable channel.
        sack_enabled:    True if SACK processing is enabled.
        bytes_inflight:  Total number of unacknowledged bytes in flight.
        srtt:            Smoothed RTT estimate (ms).
        rttvar:          RTT variance estimate (ms).
        default_rto:     Default RTO before any RTT samples (ms).
        min_rto:         Minimum RTO allowed (ms).
        rtt_min:         Minimum observed RTT (ms).
        rtt_max:         Maximum observed RTT (ms).
        rtt_sum:         Sum of all RTT samples (ms).
        rtt_cnt:         Count of RTT samples taken.
        retx_total:     Total number of retransmissions performed.
        sent_rel_segments: Number of reliable segments sent.
        sent_unrel_segments: Number of unreliable segments sent.

    Methods:
        offer(data: bytes) -> int:
            Accept data into the send buffer, segmenting as needed.
        due_packets() -> List[Packet]:
            Build and return packets due for sending now.
        on_feedback(pkt: Packet) -> None:
            Process incoming ACK/SACK feedback packets.
        inflight_bytes() -> int:
            Return the number of unacknowledged bytes currently outstanding.
        has_unacked() -> bool:
            True if there are still reliable bytes awaiting ACK/SACK.
        current_rto() -> int:
            Return the sender's current RTO (ms) based on SRTT/RTTVAR.
        metrics() -> dict:
            Return a snapshot of sender metrics (RTT/RTO and counters).
    """

    def __init__(
        self,
        mss: int,
        window: int,
        now_ms: Optional[Callable[[], int]] = None,
        prob_reliable: float = 1.0,
        sack_enabled: bool = True,
        verbose: bool = False,
        rng: Optional[random.Random] = None,
    ):
        self.mss = int(mss)
        self.win = int(window)
        self.now_ms = now_ms or monotonic_ms

        self.prob_reliable = max(0.0, min(1.0, float(prob_reliable)))
        self.sack_enabled = bool(sack_enabled)
        self.verbose = bool(verbose)
        self._rng = rng or random.Random()

        self.base_seq: Dict[ChannelType, int] = {
            ChannelType.RELIABLE: 0,
            ChannelType.UNRELIABLE: 0,
        }
        self.next_seq: Dict[ChannelType, int] = {
            ChannelType.RELIABLE: 0,
            ChannelType.UNRELIABLE: 0,
        }
        self.inflight: Dict[ChannelType, Dict[int, _Seg]] = {
            ChannelType.RELIABLE: {},
            ChannelType.UNRELIABLE: {},
        }
        self.bytes_inflight: int = 0

        self.srtt: Optional[float] = None
        self.rttvar: Optional[float] = None
        self.default_rto: int = 1000
        self.min_rto: int = 200

        self.rtt_min: Optional[float] = None
        self.rtt_max: Optional[float] = None
        self.rtt_sum: float = 0.0
        self.rtt_cnt: int = 0
        self._rtt_samples = deque(maxlen=64)

        self.retx_total: int = 0
        self.sent_rel_segments: int = 0
        self.sent_unrel_segments: int = 0

        self.start_time_ms: Optional[int] = None
        self.end_time_ms: Optional[int] = None
        self.total_packets_sent: int = 0
        self.total_packets_received: int = 0
        self.total_bytes_sent: int = 0

    def offer(self, data: bytes) -> int:
        """
        Accept as much as fits the window, segment to MSS, enqueue into inflight.

        Args:
            data (bytes): The data to offer for sending.

        Returns:
            int: The number of bytes accepted for sending.
        """
        if not data:
            return 0

        space = max(self.win - self.bytes_inflight, 0)
        if space <= 0:
            return 0

        take = min(len(data), space)
        off = 0
        while off < take:
            end = min(off + self.mss, take)
            chunk = data[off:end]
            use_rel = self._rng.random() < self.prob_reliable
            chan = ChannelType.RELIABLE if use_rel else ChannelType.UNRELIABLE
            seg = _Seg(
                seq=self.next_seq[chan],
                end=self.next_seq[chan] + len(chunk),
                chan=chan,
                payload=chunk,
            )
            self.inflight[chan][seg.seq] = seg
            self.next_seq[chan] = seg.end
            self.bytes_inflight += len(chunk)
            off = end
        return take

    def due_packets(self) -> List[Packet]:
        """
        Build DATA packets for segments due to send now (first send or after RTO).
        New segments are randomly assigned RELIABLE/UNRELIABLE by prob_reliable.
        UNRELIABLE segments are freed immediately (no feedback expected).

        Returns:
            List[Packet]: The list of packets due for sending now.
        """
        now = self.now_ms()
        out: List[Packet] = []
        to_free: List[int] = []

        # Iterate the unreliable channel
        for seg in self.inflight[ChannelType.UNRELIABLE].values():
            if seg.acked:
                continue
            first_send = seg.sent_ts == 0
            if first_send:
                self.sent_unrel_segments += 1
            else:
                seg.retx_count += 1
                self.retx_total += 1
                seg.rto_ms = min(seg.rto_ms * 2, MAXIMUM_RTO_MS)

            pkt = Packet(
                typ=PacketType.DATA,
                channel_type=seg.chan,
                seq=seg.seq,
                ts_send=now,
                payload=seg.payload,
            )
            seg.sent_ts = now
            out.append(pkt)

            if self.start_time_ms is None:
                self.start_time_ms = now
            self.end_time_ms = now
            self.total_packets_sent += 1
            self.total_bytes_sent += len(seg.payload)

            seg.acked = True
            self._print(
                f"{'RETX' if not first_send else 'TX  '} | ch={seg.chan.name} | "
                f"seq={seg.seq} len={len(seg.payload)} rto={seg.rto_ms}ms"
            )

        # Iterate the reliable channel
        for seg in sorted(
            self.inflight[ChannelType.RELIABLE].values(), key=lambda s: s.seq
        ):
            if seg.acked:
                continue

            first_send = seg.sent_ts == 0
            need_send = first_send or (now - seg.sent_ts) >= seg.rto_ms
            if not need_send:
                continue

            if first_send:
                self.sent_rel_segments += 1
            else:
                seg.retx_count += 1
                self.retx_total += 1
                seg.rto_ms = min(seg.rto_ms * 2, MAXIMUM_RTO_MS)

            pkt = Packet(
                typ=PacketType.DATA,
                channel_type=seg.chan,
                seq=seg.seq,
                ts_send=now,
                payload=seg.payload,
            )
            seg.sent_ts = now
            out.append(pkt)

            if self.start_time_ms is None:
                self.start_time_ms = now
            self.end_time_ms = now
            self.total_packets_sent += 1
            self.total_bytes_sent += len(seg.payload)

            self._print(
                f"{'RETX' if not first_send else 'TX  '} | ch={seg.chan.name} | "
                f"seq={seg.seq} len={len(seg.payload)} rto={seg.rto_ms}ms"
            )

        if to_free:
            freed = 0
            for s in to_free:
                freed += len(self.inflight[ChannelType.RELIABLE][s].payload)
                del self.inflight[ChannelType.RELIABLE][s]
            self.bytes_inflight = max(self.bytes_inflight - freed, 0)

        for s in to_free:
            freed += len(self.inflight[ChannelType.UNRELIABLE][s].payload)
            del self.inflight[ChannelType.UNRELIABLE][s]

        return out

    def on_feedback(self, pkt: Packet) -> None:
        """
        Process incoming ACK/SACK feedback packets.

        Args:
            pkt (Packet): The incoming ACK or SACK packet.

        Returns:
            None
        """
        if pkt.typ not in (PacketType.ACK, PacketType.SACK):
            return

        self._maybe_update_rtt(pkt.ts_echo)

        # Cumulative ACK
        self._ack_up_to(pkt.ack)

        # Selective ACK blocks
        if pkt.typ == PacketType.SACK and self.sack_enabled:
            for blk in pkt.sack:
                self._ack_range(blk.start, blk.end)

        # Remove all acked segments and update byte count
        freed = 0
        done = [s for s in self.inflight[ChannelType.RELIABLE].values() if s.acked]
        for seg in done:
            freed += len(seg.payload)
            del self.inflight[ChannelType.RELIABLE][seg.seq]
        self.bytes_inflight = max(self.bytes_inflight - freed, 0)

        self.total_packets_received += len(done)

    def _ack_up_to(self, up_to: int) -> None:
        """
        Mark all segments with end <= up_to as acked.

        Args:
            up_to (int): The byte offset up to which segments should be marked as acked.

        Returns:
            None
        """
        for seg in self.inflight[ChannelType.RELIABLE].values():
            if seg.end <= up_to:
                seg.acked = True

    def _ack_range(self, start: int, end: int) -> None:
        """
        Mark all segments that overlap [start, end) as acked.

        Args:
            start (int): The start of the byte range (inclusive).
            end (int): The end of the byte range (exclusive).

        Returns:
            None
        """
        for seg in self.inflight[ChannelType.RELIABLE].values():
            if seg.acked:
                continue
            if seg.seq >= end or seg.end <= start:
                continue
            seg.acked = True

    def _maybe_update_rtt(self, ts_echo: int) -> None:
        """
        Update SRTT/RTTVAR from a clean RTT sample.

        Args:
            ts_echo (int): The echoed timestamp from the feedback packet.

        Returns:
            None
        """
        if ts_echo == 0:
            return

        for seg in self.inflight[ChannelType.RELIABLE].values():
            if seg.sent_ts == ts_echo and seg.retx_count == 0:
                sample = max(self.now_ms() - ts_echo, 1)

                self.rtt_cnt += 1
                self.rtt_sum += sample
                self.rtt_min = (
                    sample if self.rtt_min is None else min(self.rtt_min, sample)
                )
                self.rtt_max = (
                    sample if self.rtt_max is None else max(self.rtt_max, sample)
                )
                self._rtt_samples.append(int(sample))

                if self.srtt is None:
                    self.srtt = float(sample)
                    self.rttvar = float(sample) / 2.0
                else:
                    alpha, beta = 1 / 8, 1 / 4
                    self.rttvar = (1 - beta) * self.rttvar + beta * abs(
                        self.srtt - sample
                    )
                    self.srtt = (1 - alpha) * self.srtt + alpha * sample

                rto = self.current_rto()
                for s in self.inflight[ChannelType.RELIABLE].values():
                    if s.retx_count == 0:
                        s.rto_ms = rto
                break

    def _print(self, msg: str) -> None:
        """
        Print a debug message prefixed with [Sender].

        Args:
            msg (str): The message to print.

        Returns:
            None
        """
        if self.verbose:
            print(f"[Sender] {msg}")

    def inflight_bytes(self) -> int:
        """
        Return the number of unacknowledged bytes currently outstanding.

        Returns:
            int: The number of unacknowledged bytes in flight.
        """
        return self.bytes_inflight

    def has_unacked(self) -> bool:
        """
        True if there are still reliable bytes awaiting ACK/SACK.

        Returns:
            bool: True if there are unacknowledged bytes in flight.
        """
        return self.bytes_inflight > 0

    def current_rto(self) -> int:
        """
        Return the sender's current RTO (ms) based on SRTT/RTTVAR.

        Returns:
            int: The current RTO in milliseconds.
        """
        if self.srtt is None:
            return self.default_rto
        var = self.rttvar or 0.0

        rto = self.srtt + max(4.0 * var, 1.0)
        return max(int(rto), self.min_rto)

    def metrics(self) -> dict:
        """
        Return a snapshot of sender metrics (RTT/RTO and counters).

        Returns:
            dict: A dictionary of sender metrics.
        """
        avg = (self.rtt_sum / self.rtt_cnt) if self.rtt_cnt else None
        throughput_bps = None
        duration_s = 0.0
        if self.start_time_ms is not None and self.end_time_ms is not None:
            duration_s = max((self.end_time_ms - self.start_time_ms) / 1000.0, 1e-6)
            throughput_bps = self.total_bytes_sent / duration_s

        return {
            "srtt_ms": int(self.srtt) if self.srtt is not None else None,
            "rttvar_ms": int(self.rttvar) if self.rttvar is not None else None,
            "rto_current_ms": self.current_rto(),
            "rtt_min_ms": int(self.rtt_min) if self.rtt_min is not None else None,
            "rtt_max_ms": int(self.rtt_max) if self.rtt_max is not None else None,
            "rtt_avg_ms": int(avg) if avg is not None else None,
            "rtt_samples_ms_last": list(self._rtt_samples),
            "retransmits": self.retx_total,
            "inflight_bytes": self.bytes_inflight,
            "segments_inflight": sum(
                1 for s in self.inflight[ChannelType.RELIABLE].values() if not s.acked
            ),
            "segments_sent_reliable": self.sent_rel_segments,
            "segments_sent_unreliable": self.sent_unrel_segments,
            "total_packets_sent": self.total_packets_sent,
            "total_packets_received": self.total_packets_received,
            "total_bytes_sent": self.total_bytes_sent,
            "duration_s": round(duration_s, 3),
            "throughput_bytes_per_sec": (
                round(throughput_bps, 2) if throughput_bps else None
            ),
        }

    def get_inflight_segments(self) -> List[_Seg]:
        """
        Return a list of all segments currently in flight.

        Returns:
            List[_Seg]: The list of segments in flight.
        """
        return list(self.inflight[ChannelType.RELIABLE].values())
