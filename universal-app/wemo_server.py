import os
import sys
import json
import time
import threading
import datetime
import socket
import logging
import ipaddress
import concurrent.futures
import requests
from flask import Flask, render_template, jsonify, request
from waitress import serve
from pywemo.ouimeaux_device.dimmer import Dimmer

# --- CONFIGURATION ---
VERSION = "v5.3.0-Server"
PORT = int(os.environ.get("PORT", 5050)) 
HOST = "0.0.0.0"
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", 300))

# --- PATH SETUP ---
if sys.platform == "win32":
    APP_DATA_DIR = os.path.join(os.getenv('APPDATA'), "WemoOps")
else:
    if os.geteuid() == 0:
        APP_DATA_DIR = "/var/lib/wemo-ops"
    else:
        APP_DATA_DIR = os.path.expanduser("~/.local/share/WemoOps")

if not os.path.exists(APP_DATA_DIR):
    try: os.makedirs(APP_DATA_DIR)
    except: pass

SCHEDULE_FILE = os.path.join(APP_DATA_DIR, "schedules.json")
SETTINGS_FILE = os.path.join(APP_DATA_DIR, "settings.json")
DEVICES_FILE = os.path.join(APP_DATA_DIR, "devices.json")

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(APP_DATA_DIR, "server.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("WemoServer")

class ScanNoiseFilter(logging.Filter):
    NOISE = ("Failed to fetch description", "Failed to parse description")
    def filter(self, record):
        msg = record.getMessage()
        return not any(n in msg for n in self.NOISE)

for handler in logging.root.handlers:
    handler.addFilter(ScanNoiseFilter())

app = Flask(__name__)

# --- GLOBAL STATE ---
device_registry = {}
scan_status = "Idle"
settings = {}
solar_times = {}

# --- UTILS ---
def load_json(path, default=None):
    if default is None: 
        default = {}
    if os.path.exists(path):
        try: 
            with open(path, 'r') as f: 
                return json.load(f)
        except: 
            pass
    return default

def save_json(path, data):
    try: 
        with open(path, 'w') as f: 
            json.dump(data, f, indent=2)
    except Exception as e: 
        logger.error(f"Failed to save JSON: {e}")

def save_device_cache():
    cache_data = {}
    for name, data in device_registry.items():
        cache_data[name] = {
            "ip": data.get("ip"),
            "mac": data.get("mac"),
            "serial": data.get("serial"),
            "state": data.get("state", 0),
            "type": data.get("type", "switch"),
            "last_seen": data.get("last_seen", 0)
        }
    save_json(DEVICES_FILE, cache_data)

def load_device_cache():
    global device_registry
    cache = load_json(DEVICES_FILE, {})
    for name, data in cache.items():
        device_registry[name] = {
            "obj": None,
            "ip": data.get("ip"),
            "mac": data.get("mac"),
            "serial": data.get("serial"),
            "state": data.get("state", 0),
            "type": data.get("type", "switch"),
            "last_seen": data.get("last_seen", 0)
        }

def get_solar_times():
    global solar_times
    if solar_times and solar_times.get('date') == datetime.date.today().isoformat():
        return solar_times
    lat = settings.get('lat'); lng = settings.get('lng')
    if not lat: return None
    try:
        url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lng}&formatted=0"
        r = requests.get(url, timeout=5)
        res = r.json()["results"]
        def to_local(utc_str):
            return datetime.datetime.fromisoformat(utc_str).astimezone().strftime("%H:%M")
        solar_times = {
            "date": datetime.date.today().isoformat(),
            "sunrise": to_local(res["sunrise"]),
            "sunset": to_local(res["sunset"])
        }
        return solar_times
    except: return None

# --- DEEP SCANNER ---
class DeepScanner:
    """Brute-force TCP sweep used when SSDP/multicast discovery misses devices."""

    WEMO_PORTS = (49153, 49152, 49154, 49155)
    PROBE_TIMEOUT = 0.35
    PROBE_WORKERS = 128
    VERIFY_TIMEOUT = 1.5
    VERIFY_WORKERS = 32

    def _probe(self, target):
        ip, port = target
        try:
            with socket.create_connection((ip, port), timeout=self.PROBE_TIMEOUT):
                return target
        except OSError:
            return None

    def probe_port(self, ip, ports=None, timeout=None):
        for port in (ports or self.WEMO_PORTS):
            if self._probe((str(ip), port)):
                return str(ip)
        return None

    def identify(self, target):
        import pywemo
        ip, port = target
        url = "http://{}:{}/setup.xml".format(ip, port)
        try:
            r = requests.get(url, timeout=self.VERIFY_TIMEOUT)
            if r.status_code != 200 or "Belkin" not in r.text:
                return None
            return pywemo.discovery.device_from_description(url)
        except Exception:
            return None

    def scan_subnet(self, subnets):
        all_hosts = []
        for subnet in subnets:
            try:
                if "/" not in subnet:
                    subnet += "/24"
                net = ipaddress.ip_network(subnet.strip(), strict=False)
                all_hosts.extend([str(ip) for ip in net.hosts()])
            except ValueError:
                pass
        if not all_hosts:
            return []

        targets = [(ip, port) for ip in all_hosts for port in self.WEMO_PORTS]
        open_targets = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.PROBE_WORKERS) as ex:
            for res in ex.map(self._probe, targets):
                if res:
                    open_targets.append(res)

        devices, seen = [], set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.VERIFY_WORKERS) as ex:
            for dev in ex.map(self.identify, open_targets):
                if dev is not None and dev.host not in seen:
                    seen.add(dev.host)
                    devices.append(dev)
        return devices

# --- BACKGROUND TASKS ---
def register_device(dev):
    global device_registry
    try:
        mac = getattr(dev, 'mac', 'Unknown')
        serial = getattr(dev, 'serial_number', 'Unknown')
        # [NEW] Check if device is a dimmer
        is_dimmer = isinstance(dev, Dimmer)
        
        device_registry[dev.name] = {
            "obj": dev,
            "ip": dev.host,
            "mac": mac,
            "serial": serial,
            "state": 0,
            "type": "dimmer" if is_dimmer else "switch",
            "last_seen": time.time()
        }
    except Exception as e:
        logger.error(f"Error registering device {dev}: {e}")

def run_scan_cycle():
    global scan_status, device_registry
    if scan_status != "Idle": return
    try:
        scan_status = "Scanning..."
        import pywemo
        ds = DeepScanner()
        load_device_cache()
        devices = pywemo.discover_devices()
        for dev in devices: register_device(dev)
        subs = settings.get("subnets", [])
        if subs:
            scan_status = "Deep Scanning..."
            deep_devs = ds.scan_subnet(subs)
            for dev in deep_devs: register_device(dev)
        now = time.time()
        to_remove = [n for n, d in device_registry.items() if (now - d.get("last_seen", 0)) > 900]
        for name in to_remove: del device_registry[name]
        save_device_cache()
        scan_status = "Idle"
    except Exception as e:
        logger.error(f"Scan Error: {e}"); scan_status = "Error"

def scanner_loop():
    while True: run_scan_cycle(); time.sleep(SCAN_INTERVAL) 

def poller_loop():
    while True:
        keys = list(device_registry.keys())
        for name in keys:
            entry = device_registry[name]
            dev = entry.get("obj")
            if dev:
                try:
                    state = dev.get_state(force_update=True)
                    entry['state'] = state
                    entry['last_seen'] = time.time()
                except: pass
            else:
                ip = entry.get("ip")
                if ip:
                    for p in [49153, 49152, 49154, 49155]:
                        try:
                            url = f"http://{ip}:{p}/setup.xml"
                            import pywemo
                            new_dev = pywemo.discovery.device_from_description(url)
                            if new_dev: register_device(new_dev); break
                        except: pass
        time.sleep(2)

def scheduler_loop():
    while True:
        try:
            now = datetime.datetime.now(); today_str = now.strftime("%Y-%m-%d"); weekday = now.weekday(); current_hhmm = now.strftime("%H:%M")
            solar = get_solar_times()
            current_schedules = load_json(SCHEDULE_FILE, []); schedule_modified = False
            for job in current_schedules:
                job_days = job.get('days', [])
                if not job_days or weekday not in job_days: continue
                trigger_time = ""
                if job['type'] == "Time (Fixed)": trigger_time = job['value']
                elif solar:
                    base = solar['sunrise'] if job['type'] == "Sunrise" else solar['sunset']
                    try:
                        dt = datetime.datetime.strptime(f"{today_str} {base}", "%Y-%m-%d %H:%M")
                        trigger_time = (dt + datetime.timedelta(minutes=int(job['value']) * int(job.get('offset_dir', 1)))).strftime("%H:%M")
                    except: continue
                if trigger_time == current_hhmm and job.get('last_run') != today_str:
                    entry = device_registry.get(job['device'])
                    if entry and entry.get("obj"):
                        dev = entry["obj"]
                        try:
                            action = job['action']
                            if action == "Turn ON": dev.on()
                            elif action == "Turn OFF": dev.off()
                            elif action == "Toggle": dev.toggle()
                            entry['state'] = dev.get_state(force_update=True)
                            logger.info(f"Executed Schedule: {job['device']} -> {action}")
                        except Exception as e: logger.error(f"Failed to execute schedule for {job['device']}: {e}")
                    job['last_run'] = today_str; schedule_modified = True
            if schedule_modified: save_json(SCHEDULE_FILE, current_schedules)
        except Exception as e: logger.error(f"Scheduler error: {e}")
        time.sleep(30)

# --- ROUTES ---
@app.route('/')
def index(): return render_template("index.html", version=VERSION)

@app.route('/api/status')
def api_status():
    return jsonify({"status": "online", "scan_status": scan_status, "device_count": len(device_registry), "version": VERSION})

@app.route('/api/devices')
def api_devices():
    devs_out = []
    for name, data in device_registry.items():
        devs_out.append({
            "name": name, 
            "ip": data.get("ip"), 
            "state": data.get("state", 0),
            "mac": data.get("mac"),
            "serial": data.get("serial"),
            "type": data.get("type", "switch") # [NEW] Return device type
        })
    return jsonify(devs_out)

@app.route('/api/toggle/<name>', methods=['POST'])
def api_toggle(name):
    entry = device_registry.get(name)
    if entry and entry.get("obj"):
        dev = entry["obj"]
        def toggle_task():
            try: dev.toggle(); entry['state'] = dev.get_state(force_update=True)
            except: pass
        threading.Thread(target=toggle_task).start()
        return jsonify({"status": "ok"})
    return jsonify({"status": "not found"}), 404

# [NEW] Brightness Route
@app.route('/api/brightness/<name>', methods=['POST'])
def api_brightness(name):
    entry = device_registry.get(name)
    if entry and entry.get("obj"):
        dev = entry["obj"]
        try:
            level = int(request.json.get('level', 0))
            def dim_task():
                try: 
                    dev.set_brightness(level)
                    entry['state'] = level
                except: pass
            threading.Thread(target=dim_task).start()
            return jsonify({"status": "ok"})
        except Exception as e: return jsonify({"status": "error", "error": str(e)}), 400
    return jsonify({"status": "not found"}), 404

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    global settings
    if request.method == 'GET': return jsonify(settings)
    if request.method == 'POST':
        settings.update(request.json); save_json(SETTINGS_FILE, settings)
        return jsonify({"status": "saved"})

@app.route('/api/scan', methods=['POST'])
def api_scan():
    global scan_status
    if scan_status != "Idle": return jsonify({"status": "busy"})
    threading.Thread(target=run_scan_cycle, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/schedules', methods=['GET', 'POST', 'DELETE'])
def api_schedules():
    current = load_json(SCHEDULE_FILE, [])
    if request.method == 'GET': return jsonify(current)
    if request.method == 'POST':
        data = request.json
        new_job = {
            "id": int(time.time()),
            "device": data.get('device'),
            "type": data.get('type'),
            "action": data.get('action'),
            "value": data.get('value'),
            "offset_dir": data.get('offset_dir', 1),
            "days": data.get('days', [0,1,2,3,4,5,6]),
            "last_run": ""
        }
        current.append(new_job); save_json(SCHEDULE_FILE, current)
        return jsonify({"status": "added", "id": new_job['id']})
    if request.method == 'DELETE':
        jid = int(request.args.get('id')); current = [x for x in current if x['id'] != jid]; save_json(SCHEDULE_FILE, current)
        return jsonify({"status": "deleted"})

if __name__ == "__main__":
    settings = load_json(SETTINGS_FILE, {})
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=poller_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print(f"   WEMO OPS SERVER - LISTENING ON PORT {PORT}")
    serve(app, host=HOST, port=PORT, threads=6)