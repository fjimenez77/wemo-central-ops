"""Combine cached device rule copies for the desktop library."""

from collections import Counter
from pathlib import Path
import json
import os
import tempfile

from wemo_native_rules import COUNTDOWN, DAYS, INTERVAL, clock_text


def save_library(path, inventory):
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(path).parent,
                                         delete=False) as temporary:
            temporary_path = temporary.name
            json.dump(inventory, temporary)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def group_native_rules(inventory):
    groups = {}
    for udn, report in inventory.items():
        for rule in report["rules"]:
            identity = (rule["id"], rule["name"], rule["kind"])
            signature = rule.get("signature")
            if signature is None:
                signature = (udn, rule["id"])
            key = identity, signature
            group = groups.setdefault(key, {"rule": rule, "copies": {}})
            group["copies"][udn] = rule
    variants = Counter(identity for identity, _ in groups)
    for (identity, _), group in groups.items():
        group["conflict"] = variants[identity] > 1
    return sorted(groups.values(), key=lambda group: group["rule"]["name"].casefold())


def controlled_devices(rule):
    # Target rows identify devices acted on by linked rules, such as long press.
    return set(rule["targets"]) or {entry[0] for entry in rule["entries"] if entry[0]}


def triggering_devices(rule):
    if rule["kind"] == "Long Press":
        return {entry[0] for entry in rule["entries"] if entry[0]}
    return set()


def paused_schedule_copies(rule, names):
    if not rule.get("copyable"):
        return []
    grouped = {}
    for target, day, start, end, *_ in rule["entries"]:
        weekday = (day - 2) % 7
        grouped.setdefault(("Turn ON", start, target), set()).add(weekday)
        grouped.setdefault(("Turn OFF", end, target), set()).add((weekday + (end < start)) % 7)
    schedules = {}
    for (action, time, target), days in grouped.items():
        schedules.setdefault((action, time, tuple(sorted(days))), []).append(
            {"device": names.get(target, target), "device_id": target})
    return [{"action": action, "type": "Time (Fixed)", "value": clock_text(time),
             "days": list(days), "targets": targets, "enabled": False}
            for (action, time, days), targets in schedules.items()]


def describe_native_rule(rule, names):
    timings = {}
    for target, day, start, end, delay, on, off in rule["entries"]:
        timings.setdefault((target, start, end, delay, on, off), set()).add(day)
    descriptions = []
    for (target, start, end, delay, on, off), days in timings.items():
        ordered_days = [DAYS[index - 1] for index in range(1, 8) if index in days]
        day_label = "Every day" if len(ordered_days) == 7 else ", ".join(ordered_days)
        if rule["kind"] == COUNTDOWN and delay and delay > 0:
            delay_text = f"{delay // 60} min" if delay % 60 == 0 else f"{delay} sec"
            timing = f"Auto-off after {delay_text}"
        elif rule["kind"] == INTERVAL and (on, off) == (1, 0):
            timing = f"On at {clock_text(start)} · Off at {clock_text(end)}"
        elif rule["kind"] == "Long Press":
            timing = "Long-press trigger"
        else:
            timing = f"{clock_text(start)}–{clock_text(end)}"
        descriptions.append(f"{names.get(target, target or 'Unknown device')} · {day_label or 'Device-defined days'} · {timing}")
    return descriptions
