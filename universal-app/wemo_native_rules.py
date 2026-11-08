"""Read and update single-device schedules stored in Wemo firmware."""

import base64
from contextlib import closing, contextmanager
import datetime
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

import requests
from pywemo.ouimeaux_device.api.rules_db import ALL_TABLES, RuleDevicesRow, RulesDb, RulesRow

INTERVAL = "Time Interval"
COUNTDOWN = "Countdown Rule"
DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
MAX_DB_SIZE = 8 * 1024 * 1024
REFERENCES = ("DEVICECOMBINATION", "BLOCKEDRULES", "RULESNOTIFYMESSAGE", "SENSORNOTIFICATION")


def fetch_archive(device):
    fetch = device.rules.FetchRules()
    version = int(fetch["ruleDbVersion"])
    url = device.session.urljoin(fetch["ruleDbPath"])
    address = urlsplit(url)
    if address.scheme != "http" or address.hostname != device.host or address.username:
        raise ValueError("The Wemo returned an unexpected rules database URL.")
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=5, allow_redirects=False, stream=True) as response:
            if response.status_code == 404 and version in (-1, 0):
                return version, b""
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("The Wemo did not return a rules database.")
            archive = bytearray()
            for chunk in response.iter_content(65536):
                archive.extend(chunk)
                if len(archive) > MAX_DB_SIZE:
                    raise ValueError("The device rules archive is too large.")
    return version, bytes(archive)


@contextmanager
def open_database(device, archive):
    with tempfile.TemporaryDirectory(prefix="wemo-native-rules-") as folder:
        path = Path(folder) / "rules.db"
        member = "temppluginRules.db"
        if archive:
            with ZipFile(io.BytesIO(archive)) as zipped:
                files = [entry for entry in zipped.infolist() if not entry.is_dir()]
                if len(files) != 1 or files[0].file_size > MAX_DB_SIZE:
                    raise ValueError("Unexpected Wemo rules archive layout.")
                member = files[0].filename
                path.write_bytes(zipped.read(files[0]))
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA trusted_schema=OFF")
            if not archive:
                for table in ALL_TABLES:
                    table.create_sqlite_table_from_row_schema(connection.cursor())
            yield RulesDb(connection, device.udn, device.name), path, member


def fingerprint(version, archive):
    return version, hashlib.sha256(archive).hexdigest()


def editable_fields(db, rule, entries, udn):
    if (rule.Type not in (INTERVAL, COUNTDOWN) or rule.State not in ("0", "1")
            or not entries or db.get_target_devices_for_rule(rule)):
        return None
    if any(entry.DeviceID != udn or entry.GroupID != 0 or entry.DayID not in range(1, 8)
           for entry in entries):
        return None
    days = sorted(entry.DayID for entry in entries)
    if len(set(days)) != len(days):
        return None
    fields = ("StartTime", "EndTime", "RuleDuration", "StartAction", "EndAction",
              "OnModeOffset", "OffModeOffset", "CountdownTime", "SensorDuration",
              "Type", "Value", "Level", "ZBCapabilityStart", "ZBCapabilityEnd")
    first = entries[0]
    if any(any(getattr(entry, key) != getattr(first, key) for key in fields) for entry in entries[1:]):
        return None
    if (first.OnModeOffset != 0 or first.OffModeOffset != 0 or first.SensorDuration != 0
            or (first.Type, first.Value, first.Level) != (-1, -1, -1)
            or (first.ZBCapabilityStart, first.ZBCapabilityEnd) != ("-1", "-1")):
        return None
    start, end = first.StartTime, first.EndTime
    if not all(isinstance(value, int) and 0 <= value < 86400 and value % 60 == 0
               for value in (start, end)) or (end - start) % 86400 != first.RuleDuration:
        return None
    if rule.Type == INTERVAL:
        if (first.StartAction, first.EndAction, first.CountdownTime) != (1, 0, 0):
            return None
    elif (start, end, first.StartAction, first.EndAction) != (60, 86340, 0, 0) or not (
            isinstance(first.CountdownTime, int) and 0 < first.CountdownTime < 86400
            and first.CountdownTime % 60 == 0):
        return None
    # Additional tables can carry references that require rule-type-specific edits.
    for table in REFERENCES:
        columns = {row["name"].lower() for row in db.db.execute(f"PRAGMA table_info({table})")}
        for column in ("ruleid", "notifyruleid"):
            if column in columns and db.db.execute(
                    f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (rule.RuleID,)).fetchone():
                return None
    return {"kind": rule.Type, "name": rule.Name, "enabled": rule.State == "1",
            "days": days, "start": clock_text(start), "end": clock_text(end),
            "minutes": first.CountdownTime // 60}


def clock_text(seconds):
    if isinstance(seconds, int) and 0 <= seconds < 86400:
        return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}"
    return str(seconds)


def read_device_rules(device):
    version, archive = fetch_archive(device)
    with open_database(device, archive) as (db, _, _):
        rules = []
        for rule in db.rules.values():
            entries = [entry for entry in db.rule_devices.values() if entry.RuleID == rule.RuleID]
            targets = sorted(db.get_target_devices_for_rule(rule))
            # Ignore local row IDs and order, but retain rule semantics for matching copies.
            metadata = [getattr(rule, field) for field in RulesRow.FIELDS
                        if field not in ("RuleID", "RuleOrder")]
            row_fields = [field for field in RuleDevicesRow.FIELDS if field not in ("RuleDevicePK", "RuleID")]
            rows = sorted(json.dumps([getattr(entry, field) for field in row_fields]) for entry in entries)
            related = {}
            for table in REFERENCES:
                columns = {column["name"].lower() for column in db.db.execute(f"PRAGMA table_info({table})")}
                references = [column for column in ("ruleid", "notifyruleid") if column in columns]
                if references:
                    query = " OR ".join(f"{column} = ?" for column in references)
                    rows_for_rule = db.db.execute(f"SELECT * FROM {table} WHERE {query}",
                                                 [rule.RuleID] * len(references))
                    related[table] = sorted(json.dumps(dict(row), sort_keys=True) for row in rows_for_rule)
            signature = hashlib.sha256(json.dumps([metadata, rows, targets, related]).encode()).hexdigest()
            by_device = {}
            for entry in entries:
                by_device.setdefault(entry.DeviceID, []).append(entry)
            copyable = (rule.Type == INTERVAL and (rule.StartDate, rule.EndDate) == ("12201982", "07301982")
                        and bool(by_device) and all(
                target and editable_fields(db, rule, device_entries, target)
                for target, device_entries in by_device.items()))
            rules.append({
                "id": rule.RuleID, "signature": signature, "copyable": copyable, "name": rule.Name, "kind": rule.Type, "state": rule.State,
                "targets": targets,
                "entries": [(entry.DeviceID, entry.DayID, entry.StartTime, entry.EndTime,
                             entry.CountdownTime, entry.StartAction, entry.EndAction) for entry in entries],
                "edit": editable_fields(db, rule, entries, device.udn),
            })
    return fingerprint(version, archive), rules


def validate_rule(values):
    name = values["name"].strip()
    days = sorted(set(values["days"]))
    kind = values["kind"]
    if (not name or len(name) > 64 or not days or any(day not in range(1, 8) for day in days)
            or kind not in (INTERVAL, COUNTDOWN)):
        raise ValueError("Enter a rule name, supported type, and at least one day.")
    if kind == INTERVAL:
        clocks = [datetime.datetime.strptime(values[key], "%H:%M") for key in ("start", "end")]
        start, end = [clock.hour * 3600 + clock.minute * 60 for clock in clocks]
        if start == end:
            raise ValueError("Start and end times must differ.")
        action, countdown = 1, 0
    else:
        countdown = int(values["minutes"]) * 60
        if not 0 < countdown < 86400:
            raise ValueError("Enter an auto-off delay from 1 to 1439 minutes.")
        start, end, action = 60, 86340, 0

    return name, days, kind, start, end, action, countdown


def change_database(db, udn, rule_id, values):
    old = db.rules.get(rule_id)
    entries = [entry for entry in db.rule_devices.values() if entry.RuleID == rule_id]
    if rule_id is not None and (old is None or editable_fields(db, old, entries, udn) is None):
        raise ValueError("Only supported rules targeting this Wemo alone can be changed.")
    if values is None:
        if old is None:
            raise ValueError("Select a rule to delete.")
        for entry in entries:
            db.remove_rule_devices(entry)
        db.remove_rule(old)
        return

    name, days, kind, start, end, action, countdown = validate_rule(values)
    if old and kind != old.Type:
        raise ValueError("The type of an existing rule cannot be changed.")

    rule = old or RulesRow(Type=kind, RuleOrder=max(
        (r.RuleOrder or 0 for r in db.rules.values()), default=0) + 1,
        StartDate="12201982", EndDate="07301982", Sync="NOSYNC")
    rule.Name = name
    rule.State = "1" if values["enabled"] else "0"
    db.add_rule(rule)
    by_day = {entry.DayID: entry for entry in entries}
    for entry in entries:
        if entry.DayID not in days:
            db.remove_rule_devices(entry)
    for day in days:
        entry = by_day.get(day) or RuleDevicesRow(
            RuleID=rule.RuleID, DeviceID=udn, GroupID=0, DayID=day,
            SensorDuration=0, Type=-1, Value=-1, Level=-1,
            ZBCapabilityStart="-1", ZBCapabilityEnd="-1", OnModeOffset=0, OffModeOffset=0)
        entry.StartTime = start
        entry.EndTime = end
        entry.RuleDuration = (end - start) % 86400
        entry.StartAction = action
        entry.EndAction = 0
        entry.CountdownTime = countdown
        db.add_rule_devices(entry)


def write_device_rule(device, expected, rule_id, values, backup_dir):
    version, archive = fetch_archive(device)
    if fingerprint(version, archive) != expected:
        raise ValueError("The device rules changed. Read them again before editing.")
    with open_database(device, archive) as (db, path, member):
        change_database(db, device.udn, rule_id, values)
        db.db.commit()
        if db.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("The edited database did not pass its integrity check.")
        expected_dump = tuple(db.db.iterdump())
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as zipped:
            zipped.write(path, member)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    device_key = hashlib.sha256(device.udn.encode()).hexdigest()[:12]
    suffix = "zip" if archive else "txt"
    backup = backup_dir / f"{device_key}-{version}-{stamp}.{suffix}"
    with backup.open("xb") as file:
        file.write(archive or b"No rules database existed before the write.\n")
    if fetch_archive(device) != (version, archive):
        raise ValueError("The device rules changed before upload. Read them again.")
    body = base64.b64encode(output.getvalue()).decode("ascii")
    try:
        device.rules.StoreRules(ruleDbVersion=version + 1, processDb=1,
                                ruleDbBody="&lt;![CDATA[" + body + "]]&gt;")
        new_version, new_archive = fetch_archive(device)
        with open_database(device, new_archive) as (db, _, _):
            if new_version != version + 1 or tuple(db.db.iterdump()) != expected_dump:
                raise ValueError("The rules read back from the device differ from the update.")
    except Exception as error:
        raise RuntimeError(f"Write could not be verified. Read the device again before retrying. Backup: {backup}") from error
    return read_device_rules(device)
