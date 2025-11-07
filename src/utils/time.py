"""
A module providing a monotonic clock in milliseconds.
"""

import time


def monotonic_ms() -> int:
    """
    Monotonic clock in milliseconds

    Returns:
        int: The current monotonic time in milliseconds.
    """
    return int(time.monotonic() * 1000)
