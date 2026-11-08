"""Schedule validation, persistence, and execution shared by both runners."""

import contextlib
import datetime
import json
import os
import tempfile
import threading
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

ACTIONS = {"Turn ON": "on", "Turn OFF": "off", "Toggle": "toggle"}
TRIGGERS = ("Time (Fixed)", "Sunrise", "Sunset")


def schedule_fields(data):
    if not isinstance(data, dict):
        raise ValueError("Each schedule must be a JSON object.")
    targets = schedule_targets(data)
    action, trigger = data.get("action"), data.get("type")
    days = data.get("days", list(range(7)))
    if not targets or action not in ACTIONS or trigger not in TRIGGERS:
        raise ValueError("Choose at least one device, an action, and a trigger.")
    if not isinstance(days, list) or not days or any(type(day) is not int or day not in range(7) for day in days):
        raise ValueError("Choose at least one valid day.")
    value = str(data.get("value", "")).strip()
    direction = data.get("offset_dir", 1)
    if trigger == TRIGGERS[0]:
        value = datetime.datetime.strptime(value, "%H:%M").strftime("%H:%M")
        direction = 1
    else:
        minutes = int(value)
        if not 0 <= minutes < 1440 or direction not in (-1, 1):
            raise ValueError("Enter a solar offset between 0 and 1439 minutes.")
        value = str(minutes)
    return {
        **targets[0], "targets": targets,
        "action": action, "type": trigger, "value": value,
        "days": sorted(set(days)), "offset_dir": direction,
        "enabled": bool(data.get("enabled", True)),
    }


def schedule_targets(job):
    raw = job.get("targets", [job])
    if not isinstance(raw, list):
        raise ValueError("Schedule targets must be a list.")
    targets = {}
    for target in raw:
        if not isinstance(target, dict):
            raise ValueError("Each target must be a device record.")
        name = str(target.get("device") or "").strip()
        udn = str(target.get("device_id") or "").strip()
        if not name:
            raise ValueError("Each target needs a device name.")
        targets.setdefault(udn or name, {"device": name, "device_id": udn})
    return list(targets.values())


def same_schedule(first, second):
    def identity(job):
        fields = schedule_fields(job)
        fields.pop("enabled")
        fields.pop("device")
        fields.pop("device_id")
        fields["targets"] = sorted(target["device_id"] or target["device"] for target in fields["targets"])
        return fields
    return identity(first) == identity(second)


def find_device(job, devices):
    if job.get("device_id"):
        return next((dev for dev in devices.values() if dev.udn == job["device_id"]), None)
    return devices.get(job["device"])


def next_run(job, now, solar, estimate_future_solar=False):
    if not job.get("enabled", True):
        return None
    try:
        for offset in range(8):
            day = now.date() + datetime.timedelta(days=offset)
            if day.weekday() not in job["days"] or day.isoformat() == job.get("last_run"):
                continue
            if job["type"] == TRIGGERS[0]:
                clock = job["value"]
                adjustment = 0
            elif solar and (offset == 0 or estimate_future_solar):
                clock = solar[job["type"].lower()]
                adjustment = int(job["value"]) * job.get("offset_dir", 1)
            else:
                continue
            time = datetime.datetime.strptime(clock, "%H:%M").time()
            # Keep the existing convention that offsets wrap within the selected day.
            adjusted = (datetime.datetime.combine(day, time) + datetime.timedelta(minutes=adjustment)).time()
            run = datetime.datetime.combine(day, adjusted)
            if run >= now:
                return run
    except (KeyError, TypeError, ValueError):
        return None
    return None


class ScheduleStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            try:
                with open(self.path, encoding="utf-8") as file:
                    return json.load(file)
            except FileNotFoundError:
                return []

    def _write(self, jobs):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as file:
            try:
                json.dump(jobs, file, indent=2)
                file.close()
                os.replace(file.name, self.path)
            finally:
                if os.path.exists(file.name):
                    os.unlink(file.name)

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            if fcntl is None:
                yield
                return
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".lock", "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                yield

    def add(self, entries, skip_duplicates=False):
        additions = [dict(schedule_fields(entry), id=uuid.uuid4().hex) for entry in entries]
        with self.transaction():
            existing = self.read()
            saved = []
            for job in additions:
                if skip_duplicates and any(same_schedule(job, other) for other in existing + saved):
                    continue
                saved.append(job)
            if saved:
                self._write(existing + saved)
        return saved

    def change(self, job_id, fields=None):
        with self.transaction():
            jobs = self.read()
            job = next(job for job in jobs if str(job["id"]) == str(job_id))
            if fields is None:
                jobs.remove(job)
            else:
                updated = {**job, **fields}
                if "targets" not in fields and ("device" in fields or "device_id" in fields):
                    if len(schedule_targets(job)) > 1:
                        raise ValueError("Use an updated app to change the devices on this schedule.")
                    updated.pop("targets", None)
                job.update(schedule_fields(updated))
            self._write(jobs)

    def run_due(self, devices, solar):
        now = datetime.datetime.now().replace(second=0, microsecond=0)
        for candidate in self.read():
            if next_run(candidate, now, solar) != now:
                continue
            with self.transaction():
                jobs = self.read()
                job = next((job for job in jobs if job["id"] == candidate["id"]), None)
                if job is None or next_run(job, now, solar) != now:
                    continue
                job["last_run"] = now.date().isoformat()
                job["last_result"] = "Started " + now.isoformat(timespec="minutes")
                self._write(jobs)
            failures = []
            succeeded = 0
            targets = schedule_targets(job)
            attempted_devices = set()
            for target in targets:
                try:
                    device = find_device(target, devices)
                    if device is None:
                        raise ValueError("Device unavailable")
                    if device.udn in attempted_devices:
                        continue
                    attempted_devices.add(device.udn)
                    getattr(device, ACTIONS[job["action"]])()
                    succeeded += 1
                except Exception as error:
                    failures.append(f"{target['device']}: {error}")
            if failures:
                result = f"{succeeded} succeeded · Failed: " + "; ".join(failures)
            else:
                result = "Succeeded" if len(targets) == 1 else f"Succeeded on {succeeded} devices"
            with self.transaction():
                jobs = self.read()
                for saved in jobs:
                    if saved["id"] == job["id"]:
                        saved["last_result"] = f"{now:%Y-%m-%d %H:%M} — {result}"
                        self._write(jobs)
                        break
