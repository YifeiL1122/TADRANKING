from __future__ import annotations

from datetime import time


def slot_to_time_window(
    slot: int,
    *,
    day_start_hour: int = 6,
    hours_per_slot: int = 2,
    n_slots: int = 8,
) -> str:
    """
    Map time_slot 1..8 to real time windows:
      1 -> 06:00–08:00
      ...
      8 -> 20:00–22:00
    For slots outside 1..n_slots, returns an empty string.
    """
    if slot < 1 or slot > n_slots:
        return ""
    start_hour = day_start_hour + hours_per_slot * (slot - 1)
    end_hour = start_hour + hours_per_slot
    start = time(start_hour, 0).strftime("%H:%M")
    end = time(end_hour, 0).strftime("%H:%M")
    return f"{start}–{end}"


def format_slot_label(slot: int) -> str:
    w = slot_to_time_window(int(slot))
    return f"Slot {int(slot)} ({w})" if w else f"Slot {int(slot)}"


