import customtkinter as ctk
import pywemo
from pywemo.ouimeaux_device.dimmer import Dimmer
import threading
import sys
import os
import shutil
import time
import json
import requests
import datetime
import socket
import ipaddress
import subprocess
import concurrent.futures
import webbrowser
import re
import tempfile
import platform
from tkinter import Listbox, Misc, filedialog, messagebox
from pathlib import Path

from wemo_native_rules import (
    COUNTDOWN, DAYS, INTERVAL, clock_text, read_device_rules, validate_rule, write_device_rule,
)
from wemo_rule_library import (
    controlled_devices, describe_native_rule, group_native_rules, paused_schedule_copies,
    save_library, triggering_devices,
)
from wemo_schedules import (
    ACTIONS, TRIGGERS, ScheduleStore, find_device, next_run, same_schedule, schedule_fields, schedule_targets,
)
import pyperclip

# --- QR Code & Image Support (CRASH FIX) ---
HAS_QR = False
try:
    import qrcode
    from PIL import Image, ImageTk 
    HAS_QR = True
except Exception as e:
    print(f"QR Support Disabled: {e}")
    HAS_QR = False

# --- CONFIGURATION ---
APP_NAME = "Wemo Central Ops"
VERSION = "v5.3.1"
SERVER_PORT = 5050
HOOBS_PORT = 8581
SERVER_URL = f"http://localhost:{SERVER_PORT}"
HOOBS_URL = f"http://localhost:{HOOBS_PORT}"
RELEASE_REPO = os.environ.get("WEMO_CENTRAL_OPS_RELEASE_REPO", "").strip()
UPDATE_API_URL = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
UPDATE_PAGE_URL = f"https://github.com/{RELEASE_REPO}/releases"

# --- PATH SETUP ---
if sys.platform == "darwin":
    APP_DATA_DIR = os.path.expanduser("~/Library/Application Support/WemoOps")
elif sys.platform == "win32":
    APP_DATA_DIR = os.path.join(os.getenv('APPDATA'), "WemoOps")
else:
    APP_DATA_DIR = os.path.expanduser("~/.local/share/WemoOps")

if not os.path.exists(APP_DATA_DIR):
    try: os.makedirs(APP_DATA_DIR)
    except: pass

PROFILE_FILE = os.path.join(APP_DATA_DIR, "wifi_profiles.json")
SCHEDULE_FILE = os.path.join(APP_DATA_DIR, "schedules.json")
SETTINGS_FILE = os.path.join(APP_DATA_DIR, "settings.json")
DEVICE_CACHE_FILE = os.path.join(APP_DATA_DIR, "desktop-devices.json")
RULE_LIBRARY_FILE = os.path.join(APP_DATA_DIR, "rule-library.json")

# --- SERVICE & INSTALLER DETECTION ---
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

SERVICE_EXE_PATH = None
local_service = os.path.join(BASE_DIR, "wemo_service.exe")
mac_service = os.path.join(BASE_DIR, "wemo_service")

if os.path.exists(local_service):
    SERVICE_EXE_PATH = local_service
elif os.path.exists(mac_service):
    SERVICE_EXE_PATH = mac_service
elif sys.platform == "win32":
    appdata_service = os.path.join(APP_DATA_DIR, "wemo_service.exe")
    if os.path.exists(appdata_service):
        SERVICE_EXE_PATH = appdata_service
else:
    possible_paths = [
        os.path.join(APP_DATA_DIR, "wemo_service"),
        "/opt/WemoOps/wemo_service",
        "/usr/bin/wemo_service"
    ]
    for p in possible_paths:
        if os.path.exists(p):
            SERVICE_EXE_PATH = p
            break

if getattr(sys, 'frozen', False):
    os.environ['PATH'] += os.pathsep + sys._MEIPASS

# --- STYLING CONSTANTS ---
COLOR_BG = ("#ebebeb", "#242424")           
COLOR_SIDEBAR = ("#d6d6d6", "#1a1a1a")      
COLOR_CARD = ("#ffffff", "#2b2b2b")         
COLOR_FRAME = ("#cccccc", "#333333")        
COLOR_TEXT = ("#1a1a1a", "#ffffff")         
COLOR_SUBTEXT = ("#404040", "#aaaaaa")      
COLOR_ACCENT = ("#1f6aa5", "#1f6aa5")       
COLOR_SUCCESS = ("#2d8a4e", "#28a745")      
COLOR_DANGER = ("#c0392b", "#aa3333")       
COLOR_UPDATE = ("#d35400", "#e67e22")       
COLOR_BTN_SECONDARY = ("#e0e0e0", "#3a3a3a") 
COLOR_BTN_TEXT = ("#1a1a1a", "#ffffff")
COLOR_MAINT_BTN_Y = ("#d68910", "#e6b800") 
COLOR_MAINT_BTN_B = ("#2874a6", "#5599ee")
COLOR_MAINT_BTN_R = ("#922b21", "#cc0000")

FONT_H1 = ("Roboto", 24, "bold")
FONT_H2 = ("Roboto", 18, "bold")
FONT_BODY = ("Roboto", 14)
FONT_MONO = ("Consolas", 13)

# ==============================================================================
#  UPDATE MANAGER
# ==============================================================================
class UpdateManager:
    @staticmethod
    def check_for_updates(current_version, api_url):
        try:
            response = requests.get(api_url, timeout=3)
            if response.status_code == 200:
                data = response.json()
                latest_tag = data.get("tag_name", "").strip()
                if latest_tag and latest_tag != current_version:
                    return True, latest_tag
        except: pass
        return False, None

# ==============================================================================
#  API CLIENT
# ==============================================================================
class APIClient:
    def __init__(self):
        self.connected = False

    def check_connection(self):
        try:
            r = requests.get(f"{SERVER_URL}/api/status", timeout=0.5)
            if r.status_code == 200:
                self.connected = True
                return True
        except: pass
        self.connected = False
        return False

    def get_devices(self):
        try: return requests.get(f"{SERVER_URL}/api/devices", timeout=1).json()
        except: return []

    def get_schedules(self):
        try: return requests.get(f"{SERVER_URL}/api/schedules", timeout=2).json()
        except: return []

# ==============================================================================
#  NETWORK UTILS
# ==============================================================================
class NetworkUtils:
    @staticmethod
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except: return "127.0.0.1"

    @staticmethod
    def get_subnet_cidr():
        if sys.platform == "darwin":
            return NetworkUtils.get_mac_subnet_cidr()
        try:
            ip = NetworkUtils.get_local_ip()
            return f"{ip}/24"
        except: return "192.168.1.0/24"

    @staticmethod
    def get_mac_subnet_cidr():
        try:
            interfaces = subprocess.check_output(
                ["/sbin/ifconfig", "-a"], text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return ""

        subnets = {}
        # macOS names Wi-Fi and Ethernet enN. VPN tunnels (utun, ipsec,
        # and ppp) are intentionally excluded even if they own the default route.
        for interface in re.split(r"\n(?=\S)", interfaces):
            name = re.match(r"(en\d+):.*<([^>]+)>", interface)
            if not name or "UP" not in name[2].split(",") or "status: inactive" in interface:
                continue
            for address, mask in re.findall(r"\binet ([\d.]+) netmask (0x[\da-fA-F]+)", interface):
                ip = ipaddress.IPv4Address(address)
                if ip.is_link_local or ip.is_loopback or ip.is_unspecified:
                    continue
                netmask = ipaddress.IPv4Address(int(mask, 16))
                subnets[name[1]] = str(ipaddress.IPv4Network((address, str(netmask)), strict=False))
                break
        if not subnets:
            return ""
        if len(subnets) > 1:
            try:
                state = subprocess.check_output(
                    ["/usr/sbin/scutil"], input="show State:/Network/Global/IPv4\n",
                    text=True, timeout=2)
                primary = re.search(r"PrimaryInterface\s*:\s*(\S+)", state)
                if primary and primary[1] in subnets:
                    return subnets[primary[1]]
            except (OSError, subprocess.SubprocessError):
                pass
        return subnets[min(subnets, key=lambda name: int(name[2:]))]

    @staticmethod
    def scan_wifi_networks():
        wemos = []
        try:
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                output = subprocess.check_output("netsh wlan show networks mode=bssid", startupinfo=si).decode('utf-8', errors='ignore')
                for line in output.split('\n'):
                    if "SSID" in line:
                        ssid = line.split(":", 1)[1].strip()
                        if ssid: wemos.append(ssid)
            elif sys.platform == "darwin":
                 airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
                 if os.path.exists(airport):
                     output = subprocess.check_output(f"{airport} -s", shell=True).decode('utf-8', errors='ignore')
                     for line in output.split('\n')[1:]:
                         parts = line.strip().split()
                         if parts: wemos.append(parts[0])
            elif sys.platform.startswith("linux"):
                output = subprocess.check_output("nmcli -t -f SSID dev wifi", shell=True).decode('utf-8', errors='ignore')
                for line in output.split('\n'):
                    if line.strip(): wemos.append(line.strip())
        except: pass
        return [ssid for ssid in list(set(wemos)) if "wemo" in ssid.lower() or "belkin" in ssid.lower()]

    @staticmethod
    def check_hoobs_status():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            result = s.connect_ex(('localhost', HOOBS_PORT))
            s.close()
            return result == 0
        except: return False

class DeepScanner:
    """Brute-force TCP sweep used when SSDP/multicast discovery misses devices."""

    # A Wemo serves setup.xml on one of these; 49153 is by far the most common.
    WEMO_PORTS = (49153, 49152, 49154, 49155)
    PROBE_TIMEOUT = 0.35    # seconds per host:port TCP connect
    PROBE_WORKERS = 128     # host:port pairs probed concurrently
    VERIFY_TIMEOUT = 1.5    # seconds per setup.xml fetch
    VERIFY_WORKERS = 32

    def _probe(self, target):
        """target is (ip, port). Returns target if the port accepts a connection."""
        ip, port = target
        try:
            with socket.create_connection((ip, port), timeout=self.PROBE_TIMEOUT):
                return target
        except OSError:
            return None

    def probe_port(self, ip, ports=None, timeout=None):
        """Back-compat helper: first open Wemo port on `ip`, or None."""
        for port in (ports or self.WEMO_PORTS):
            if self._probe((str(ip), port)):
                return str(ip)
        return None

    def identify(self, target):
        """Fetch setup.xml with a hard timeout; return a pywemo device or None.

        Ports 49152-49155 are the generic UPnP ephemeral range, so most open
        ports belong to routers, printers and NAS boxes rather than Wemos. The
        cheap Belkin check keeps us out of pywemo's default (long) timeout.
        """
        ip, port = target
        url = "http://{}:{}/setup.xml".format(ip, port)
        try:
            r = requests.get(url, timeout=self.VERIFY_TIMEOUT)
            if r.status_code != 200 or "Belkin" not in r.text:
                return None
            return pywemo.discovery.device_from_description(url)
        except Exception:
            return None

    def scan_subnet(self, target_cidr, status_callback=None):
        try:
            hosts = list(ipaddress.ip_network(target_cidr, strict=False).hosts())
        except ValueError:
            return []

        # Probe each host:port pair as its own task. Probing the four ports in
        # parallel rather than in series means a dead host costs one timeout
        # instead of four.
        targets = [(str(ip), port) for ip in hosts for port in self.WEMO_PORTS]
        if status_callback:
            status_callback("Probing {} IPs...".format(len(hosts)))

        open_targets = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.PROBE_WORKERS) as ex:
            for res in ex.map(self._probe, targets):
                if res:
                    open_targets.append(res)

        if status_callback:
            status_callback("Verifying {} hosts...".format(len(open_targets)))

        found, seen = [], set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.VERIFY_WORKERS) as ex:
            for dev in ex.map(self.identify, open_targets):
                if dev is not None and dev.host not in seen:
                    seen.add(dev.host)
                    found.append(dev)
        return found

# ==============================================================================
#  WIFI AUTOMATOR
# ==============================================================================
class WifiAutomator:
    @staticmethod
    def can_automate(): return sys.platform in ["win32", "linux", "darwin"]
    
    @staticmethod
    def connect_open_network(ssid):
        try:
            if sys.platform == "win32":
                hex_ssid = ssid.encode('utf-8').hex()
                xml = f"""<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1"><name>{ssid}</name><SSIDConfig><SSID><hex>{hex_ssid}</hex><name>{ssid}</name></SSID></SSIDConfig><connectionType>ESS</connectionType><connectionMode>manual</connectionMode><MSM><security><authEncryption><authentication>open</authentication><encryption>none</encryption><useOneX>false</useOneX></authEncryption></security></MSM></WLANProfile>"""
                fd, path = tempfile.mkstemp(suffix=".xml")
                with os.fdopen(fd, 'w') as tmp: tmp.write(xml)
                subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={path}'], check=True, creationflags=0x08000000)
                os.remove(path)
                subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], check=True, creationflags=0x08000000)
                return True
            elif sys.platform.startswith("linux"):
                subprocess.run(['nmcli', 'dev', 'wifi', 'connect', ssid], check=True)
                return True
            elif sys.platform == "darwin":
                subprocess.run(['networksetup', '-setairportnetwork', 'en0', ssid], check=True)
                return True
        except: return False
        return False

# ==============================================================================
#  SOLAR ENGINE
# ==============================================================================
class SolarEngine:
    def __init__(self):
        self.lat = None; self.lng = None; self.solar_times = {}; self.last_fetch = None
    
    def get_solar_times(self):
        today = datetime.date.today()
        if self.last_fetch == today: return self.solar_times
        if not self.lat:
            try:
                r = requests.get("https://ipinfo.io/json", timeout=2)
                loc = r.json().get("loc", "").split(",")
                self.lat, self.lng = loc[0], loc[1]
            except: return None
        try:
            url = f"https://api.sunrise-sunset.org/json?lat={self.lat}&lng={self.lng}&formatted=0"
            r = requests.get(url, timeout=5)
            res = r.json()["results"]
            def to_local(utc_str):
                return datetime.datetime.fromisoformat(utc_str).astimezone().strftime("%H:%M")
            self.solar_times = {"sunrise": to_local(res["sunrise"]), "sunset": to_local(res["sunset"])}
            self.last_fetch = today
            return self.solar_times
        except: return None

# ==============================================================================
#  MAIN APP
# ==============================================================================
class WemoOpsApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("1100x800")
        
        self.set_icon(self)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.api = APIClient() 
        self.settings = self.load_json(SETTINGS_FILE, dict)
        self.profiles = self.load_json(PROFILE_FILE, dict)
        self.schedule_store = ScheduleStore(SCHEDULE_FILE)
        self.schedules = self.load_json(SCHEDULE_FILE, list)
        cached_library = self.load_json(RULE_LIBRARY_FILE, dict)
        if not isinstance(cached_library, dict):
            cached_library = {}
        self.rule_inventory = cached_library.get("devices", {})
        self.server_schedules = cached_library.get("service", [])
        self.inventory_pending = {}
        self.inventory_errors = set()
        self.inventory_save_error = False
        self.schedule_server_status = None
        self.editing_schedule = None
        self.selected_schedule_targets = []
        self.schedule_view_key = None
        self.native_snapshot = None
        self.native_busy = False
        self.saved_subnets = self.settings.get("subnets", [])
        
        self.known_devices_map = {}
        self.device_switches = {}
        cached = self.load_json(DEVICE_CACHE_FILE, dict)
        self.device_cache = {
            udn: record for udn, record in cached.items()
            if isinstance(record, dict) and isinstance(record.get("name"), str)
            and isinstance(record.get("host"), str)
        } if isinstance(cached, dict) else {}
        self.live_devices = {}
        self.scan_in_progress = False
        self.device_checking = bool(self.device_cache)
        self.last_rendered_devices = None
        self.cache_written_at = 0
        self.solar = SolarEngine()
        self.scanner = DeepScanner()
        # Deep sweep stays on by default so discovery does not regress, but it
        # is now cheap enough (~4s on a /24) that it no longer blocks the UI.
        self.use_deep_scan = True
        self.current_setup_ip = None
        self.current_setup_port = None
        self.manual_override_active = False
        self.hoobs_online = False

        if "lat" in self.settings:
            self.solar.lat = self.settings["lat"]
            self.solar.lng = self.settings["lng"]

        ctk.set_appearance_mode(self.settings.get("theme", "System"))
        self.set_ui_scale(self.settings.get("scale", "100%"))

        # --- UI SETUP ---
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=COLOR_SIDEBAR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        
        icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "images" / "WemoCentralOps.png"
        if HAS_QR and icon_path.is_file():
            with Image.open(icon_path) as icon:
                self.brand_icon = ctk.CTkImage(light_image=icon.copy(), size=(52, 52))
            ctk.CTkLabel(self.sidebar, text="", image=self.brand_icon).pack(pady=(14, 0))
        self.logo = ctk.CTkLabel(self.sidebar, text=APP_NAME, font=("Arial Black", 16), text_color=COLOR_TEXT)
        self.logo.pack(pady=(4, 15))

        self.btn_dash = self.create_nav_btn("Dashboard", "dash")
        self.btn_prov = self.create_nav_btn("Provisioner", "prov")
        self.btn_sched = self.create_nav_btn("Automation", "sched")
        self.btn_maint = self.create_nav_btn("Maintenance", "maint")
        self.btn_bridge = self.create_nav_btn("Integrations", "bridge")
        self.btn_settings = self.create_nav_btn("Settings", "settings") 
        
        ctk.CTkFrame(self.sidebar, fg_color="transparent").pack(expand=True)
        
        if HAS_QR:
            ctk.CTkButton(self.sidebar, text="📱 Mobile App", fg_color=COLOR_ACCENT, command=self.show_qr_code).pack(pady=5, padx=10)

        self.service_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.service_frame.pack(side="bottom", fill="x", pady=20, padx=10)
        self.svc_lbl = ctk.CTkLabel(self.service_frame, text="Service Status:", font=("Arial", 12, "bold"), text_color=COLOR_TEXT)
        self.svc_lbl.pack(anchor="w")
        self.svc_status = ctk.CTkLabel(self.service_frame, text="Checking...", text_color="gray", font=FONT_BODY)
        self.svc_status.pack(anchor="w")
        
        self.btn_start_svc = ctk.CTkButton(self.service_frame, text="▶ Start Service", width=100, fg_color="#2d8a4e", command=self.start_local_server)
        self.btn_start_svc.pack(pady=(5,0), anchor="w")

        self.btn_update = ctk.CTkButton(self.sidebar, text="⬇ Update Available", fg_color=COLOR_UPDATE, 
                                        font=FONT_BODY, command=lambda: webbrowser.open(UPDATE_PAGE_URL))
        self.btn_update.pack(side="bottom", padx=10, pady=(0, 10))
        self.btn_update.pack_forget()

        ctk.CTkLabel(self.sidebar, text=f"{VERSION}", text_color="gray", font=("Arial", 10)).pack(side="bottom", pady=5)

        self.main_area = ctk.CTkFrame(self, corner_radius=0, fg_color=COLOR_BG)
        self.main_area.grid(row=0, column=1, sticky="nsew")
        self.content = ctk.CTkFrame(self.main_area, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=20, pady=20)

        self.frames = {}
        self.create_dashboard()
        self.create_provisioner()
        self.create_schedule_ui()
        self.create_maintenance_ui()
        self.create_settings_ui()
        self.create_bridges_ui()
        
        self.bind_automation_keys()
        self.show_tab("dash")
        self.render_devices()
        self.after(500, self.refresh_network)
        self.after(300000, self.periodic_rule_refresh)
        
        self.monitoring = True
        threading.Thread(target=self._connection_monitor, daemon=True).start()
        threading.Thread(target=self._scheduler_engine, daemon=True).start()
        threading.Thread(target=self.run_update_check, daemon=True).start()
        threading.Thread(target=self._state_poller, daemon=True).start()
        threading.Thread(target=self._hoobs_monitor, daemon=True).start()
        
        self.server_heartbeat()

    # --- HELPERS ---
    def load_json(self, p, t): 
        if os.path.exists(p): 
            try: 
                with open(p) as f: return json.load(f)
            except: pass
        return t()
        
    def save_json(self, p, d):
        with open(p, 'w') as f: json.dump(d, f)
        
    def set_ui_scale(self, s):
        try: ctk.set_widget_scaling(int(str(s).replace("%", ""))/100)
        except: pass

    def create_nav_btn(self, text, view_name):
        btn = ctk.CTkButton(self.sidebar, text=f"  {text}", anchor="w", 
                            font=FONT_BODY, height=40,
                            fg_color="transparent", text_color=COLOR_TEXT,
                            hover_color=COLOR_FRAME,
                            command=lambda: self.show_tab(view_name))
        btn.pack(pady=2, padx=10, fill="x")
        setattr(self, f"btn_{view_name}", btn)
        return btn
        
    def show_tab(self, name):
        for key, frame in self.frames.items(): frame.pack_forget()
        self.frames[name].pack(fill="both", expand=True)
        if name == "sched":
            self.render_jobs()
        for key in ["dash", "prov", "sched", "maint", "settings", "bridge"]:
            btn = getattr(self, f"btn_{key}")
            btn.configure(fg_color="transparent", text_color=COLOR_TEXT)
        active_btn = getattr(self, f"btn_{name}")
        active_btn.configure(fg_color=COLOR_FRAME, text_color=COLOR_TEXT)

    def server_heartbeat(self):
        self.api.check_connection()
        try:
            if self.api.connected:
                self.svc_status.configure(text="✅ RUNNING", text_color="#28a745")
                self.btn_start_svc.pack_forget()
            else:
                self.svc_status.configure(text="⚠️ STOPPED", text_color="orange")
                self.btn_start_svc.pack(pady=(5,0), anchor="w")
        except: pass
        self.after(5000, self.server_heartbeat)

    def set_icon(self, window=None):
        target = window if window else self
        try:
            if getattr(sys, 'frozen', False):
                base_path = os.path.join(sys._MEIPASS, "images")
            else:
                base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
            icon_path = os.path.join(base_path, "app_icon.ico")
            if os.path.exists(icon_path) and sys.platform == "win32":
                target.iconbitmap(icon_path)
        except Exception: pass

    # --- DASHBOARD ---
    def create_dashboard(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["dash"] = f
        head = ctk.CTkFrame(f, fg_color="transparent"); head.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(head, text="Network Overview (Local)", font=FONT_H1, text_color=COLOR_TEXT).pack(side="left", anchor="n")
        
        ctrl = ctk.CTkFrame(head, fg_color="transparent"); ctrl.pack(side="right", anchor="e")
        r1 = ctk.CTkFrame(ctrl, fg_color="transparent"); r1.pack(side="top", anchor="e")
        
        self.subnet_combo = ctk.CTkComboBox(r1, width=180, values=self.saved_subnets)
        self.subnet_combo.pack(side="left", padx=2)
        self.detected_subnet = NetworkUtils.get_subnet_cidr()
        self.subnet_combo.set(self.detected_subnet)
        
        ctk.CTkButton(r1, text="Save", width=50, fg_color=COLOR_ACCENT, command=self.save_subnet).pack(side="left", padx=2)
        ctk.CTkButton(r1, text="Del", width=50, fg_color=COLOR_DANGER, command=self.delete_subnet).pack(side="left", padx=(2, 10))
        self.deep_chk = ctk.CTkCheckBox(r1, text="Deep", width=58, checkbox_width=18, checkbox_height=18,
                                       font=("Arial", 11), text_color=COLOR_TEXT,
                                       command=self._toggle_deep_scan)
        self.deep_chk.select()
        self.deep_chk.pack(side="left", padx=(2, 6))
        ctk.CTkButton(r1, text="Scan", width=80, command=self.run_local_scan, fg_color=COLOR_ACCENT).pack(side="left", padx=5)
        
        self.scan_status = ctk.CTkLabel(ctrl, text="Ready", text_color="orange", font=("Arial", 11))
        self.scan_status.pack(side="top", anchor="e", padx=10, pady=(2, 0))
        
        self.dev_list = ctk.CTkScrollableFrame(f, label_text="Devices", label_text_color=COLOR_TEXT); self.dev_list.pack(fill="both", expand=True)

    def _toggle_deep_scan(self):
        self.use_deep_scan = bool(self.deep_chk.get())

    def save_subnet(self):
        s = self.subnet_combo.get().strip()
        if s and s not in self.saved_subnets:
            self.saved_subnets.append(s)
            self.settings["subnets"] = self.saved_subnets
            self.save_json(SETTINGS_FILE, self.settings)
            self.subnet_combo.configure(values=self.saved_subnets)
            self.scan_status.configure(text="Subnet Saved")
            
    def delete_subnet(self):
        s = self.subnet_combo.get().strip()
        if s in self.saved_subnets:
            self.saved_subnets.remove(s)
            self.settings["subnets"] = self.saved_subnets
            self.save_json(SETTINGS_FILE, self.settings)
            self.subnet_combo.configure(values=self.saved_subnets)
            self.detected_subnet = NetworkUtils.get_subnet_cidr()
            self.subnet_combo.set(self.detected_subnet)
            self.scan_status.configure(text="Subnet Deleted")

    def run_local_scan(self):
        if self.scan_in_progress:
            return
        subnet = self.subnet_combo.get().strip()
        if sys.platform == "darwin" and (not subnet or subnet == self.detected_subnet):
            self.detected_subnet = subnet = NetworkUtils.get_subnet_cidr()
            self.subnet_combo.set(subnet)
        use_service = self.api.connected and (sys.platform != "darwin" or not subnet)
        self.scan_in_progress = True
        self.device_checking = True
        self.live_devices = {}
        self.known_devices_map = {}
        self.render_devices()
        self.scan_status.configure(text="Checking saved devices…")
        threading.Thread(
            target=self._scan_task,
            args=(subnet, self.use_deep_scan, use_service, self.device_cache.copy()),
            daemon=True,
        ).start()

    def _verify_device(self, device, expected_udn=None):
        try:
            if device is not None and device.udn and (expected_udn is None or expected_udn == device.udn):
                state = device.get_state(force_update=True)
                if state is not None:
                    return device, state
        except Exception:
            pass
        return None

    @staticmethod
    def address_on_subnet(host, subnet):
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            return False

    def _verify_cached_device(self, item, subnet):
        udn, record = item
        host = record["host"]
        if subnet and not self.address_on_subnet(host, subnet):
            return None
        for port in DeepScanner.WEMO_PORTS:
            device = self.scanner.identify((host, port))
            result = self._verify_device(device, udn)
            if result is not None:
                return result
        return None

    def _scan_task(self, subnet, use_deep, use_service, cache):
        def log(text):
            self.after(0, lambda: self.scan_status.configure(text=text))

        message = "Scan complete"
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                checked = [item for item in pool.map(
                    lambda entry: self._verify_cached_device(entry, subnet), cache.items()) if item]
                verified = {device.udn for device, _ in checked}
                self.after(0, self._merge_verified_devices, checked)

                found = []
                if use_service:
                    log("Fetching from server…")
                    targets = [(row["ip"], port) for row in (self.api.get_devices() or [])
                               if row.get("ip") for port in DeepScanner.WEMO_PORTS]
                    found = [device for device in pool.map(self.scanner.identify, targets) if device]
                if not found:
                    log("SSDP scan…")
                    found = list(pywemo.discover_devices())
                    if subnet and (use_deep or not found):
                        if sys.platform == "darwin" and ipaddress.ip_network(subnet, strict=False).num_addresses > 65536:
                            message = "Deep scan skipped (enter a /16 or smaller subnet)"
                        else:
                            found.extend(self.scanner.scan_subnet(subnet, status_callback=log))
                by_id = {device.udn: device for device in found if device.udn and device.udn not in verified}
                checked = [item for item in pool.map(self._verify_device, by_id.values()) if item]
                self.after(0, self._merge_verified_devices, checked)
        except Exception as error:
            message = f"Scan incomplete: {error}"
        finally:
            self.after(0, self._finish_device_scan, message)

    def _merge_verified_devices(self, checked):
        for device, state in checked:
            self.live_devices[device.udn] = device
            self.device_cache[device.udn] = {
                "name": device.name, "host": device.host, "state": state,
                "last_seen": datetime.datetime.now().isoformat(timespec="seconds"),
            }
        self._refresh_device_views()

    def _refresh_device_views(self):
        self.known_devices_map = {device.name: device for device in self.live_devices.values()}
        self.render_devices()
        self.update_maint_dropdown()
        self.update_schedule_dropdown()

    def _save_device_cache(self):
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=APP_DATA_DIR, delete=False) as file:
                try:
                    json.dump(self.device_cache, file)
                    file.close()
                    os.replace(file.name, DEVICE_CACHE_FILE)
                finally:
                    if os.path.exists(file.name):
                        os.unlink(file.name)
        except OSError:
            return False
        self.cache_written_at = time.monotonic()
        return True

    def _finish_device_scan(self, message):
        self.device_checking = False
        self.scan_in_progress = False
        if not self._save_device_cache():
            message += " · Could not save device cache"
        self.scan_status.configure(text=f"{message} ({len(self.live_devices)} online)")
        self.render_devices()
        self.refresh_rule_inventory()

    def refresh_network(self):
        self.run_local_scan()

    def render_devices(self):
        subnet = self.detected_subnet
        entries = sorted(self.device_cache.items(), key=lambda item: (
            not self.address_on_subnet(item[1]["host"], subnet), item[1]["name"].casefold()))
        snapshot = [(udn, record["name"], record["host"],
                     self.live_devices.get(udn), self.device_checking if udn not in self.live_devices else False)
                    for udn, record in entries]
        if snapshot == self.last_rendered_devices:
            return
        self.last_rendered_devices = snapshot
        self.device_switches = {}
        for widget in self.dev_list.winfo_children():
            widget.destroy()
        if not entries:
            ctk.CTkLabel(self.dev_list, text="No devices found.", text_color=COLOR_TEXT).pack(pady=20)
        for udn, record in entries:
            device = self.live_devices.get(udn)
            if device is not None:
                self.build_device_card(device, record["state"])
                continue
            card = ctk.CTkFrame(self.dev_list, fg_color=COLOR_CARD)
            card.pack(fill="x", pady=5, padx=5)
            state = "Checking…" if self.device_checking else "Offline"
            last_seen = str(record.get("last_seen") or "Unknown").replace("T", " ")
            power = "On" if record.get("state") else "Off"
            ctk.CTkLabel(card, text=f"{record['name']} · {state}", font=FONT_H2).pack(anchor="w", padx=10)
            ctk.CTkLabel(card, text=f"IP: {record['host']} · Last known: {power} · Last seen: {last_seen}").pack(anchor="w", padx=10)

    def build_device_card(self, dev, state):
        try: mac = getattr(dev, 'mac', "Unknown")
        except: mac = "Unknown"
        try: serial = getattr(dev, 'serial_number', "Unknown")
        except: serial = "Unknown"

        c = ctk.CTkFrame(self.dev_list, fg_color=COLOR_CARD, border_width=1, border_color=COLOR_FRAME); c.pack(fill="x", pady=5, padx=5)
        t = ctk.CTkFrame(c, fg_color="transparent"); t.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(t, text=dev.name, font=FONT_H2, text_color=COLOR_TEXT).pack(side="left")
        
        def tog(d=dev): threading.Thread(target=d.toggle, daemon=True).start()
        sw = ctk.CTkSwitch(t, text="Power", command=tog, text_color=COLOR_TEXT)
        sw.pack(side="right")
        ctk.CTkButton(t, text="Rules", width=65, command=lambda: self.show_device_rules(dev)).pack(side="right", padx=10)
        self.device_switches[dev.udn] = sw
        if state > 0:
            sw.select()

        if isinstance(dev, Dimmer):
            dim_frame = ctk.CTkFrame(c, fg_color="transparent")
            dim_frame.pack(fill="x", padx=10, pady=(0, 10))
            
            ctk.CTkLabel(dim_frame, text="Brightness:", font=("Arial", 12), text_color=COLOR_SUBTEXT).pack(side="left")
            
            def set_brightness(val, d=dev):
                def t():
                    try: d.set_brightness(int(val))
                    except: pass
                threading.Thread(target=t, daemon=True).start()
                switch = self.device_switches.get(d.udn)
                if switch is not None:
                    if int(val) > 0:
                        switch.select()
                    else:
                        switch.deselect()

            slider = ctk.CTkSlider(dim_frame, from_=0, to=100, command=set_brightness)
            try: slider.set(state if state else 0)
            except: slider.set(0)
            slider.pack(side="right", fill="x", expand=True, padx=10)

        m = ctk.CTkFrame(c, fg_color="transparent"); m.pack(fill="x", padx=10)
        ctk.CTkLabel(m, text=f"IP: {dev.host} | MAC: {mac} | SN: {serial}", font=FONT_MONO, text_color=COLOR_SUBTEXT).pack(anchor="w")
        
        bot = ctk.CTkFrame(c, fg_color="transparent"); bot.pack(fill="x", padx=10, pady=(5, 10))
        
        def rename_action():
            new_name = ctk.CTkInputDialog(text="Name:", title="Rename").get_input()
            if new_name: threading.Thread(target=self._rename_task, args=(dev, new_name), daemon=True).start()
        ctk.CTkButton(bot, text="> Rename", width=80, height=24, fg_color=COLOR_BTN_SECONDARY, text_color=COLOR_BTN_TEXT, command=rename_action).pack(side="left", padx=(0, 10))
        
        def extract_hk(): threading.Thread(target=self._extract_hk_task, args=(dev,), daemon=True).start()
        ctk.CTkButton(bot, text="Get HomeKit Code", width=120, height=24, fg_color=COLOR_BTN_SECONDARY, text_color=COLOR_BTN_TEXT, command=extract_hk).pack(side="left")

    def _rename_task(self, dev, new_name):
        try:
            if hasattr(dev, 'change_friendly_name'): dev.change_friendly_name(new_name)
            elif hasattr(dev, 'basicevent'): dev.basicevent.ChangeFriendlyName(FriendlyName=new_name)
            self.after(0, lambda: messagebox.showinfo("Success", "Renamed."))
            self.after(0, self.run_local_scan)
        except Exception as e: self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _extract_hk_task(self, dev):
        try:
            if hasattr(dev, 'basicevent'):
                data = dev.basicevent.GetHKSetupInfo()
                code = data.get('HKSetupCode')
                if code:
                    self.after(0, lambda: messagebox.showinfo("Code", code))
                    try: pyperclip.copy(code)
                    except: pass
                else: self.after(0, lambda: messagebox.showwarning("Error", "No Code Found"))
        except: pass

    # --- INTEGRATIONS (HOOBS) ---
    def create_bridges_ui(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["bridge"] = f
        
        h_frame = ctk.CTkFrame(f, fg_color="transparent"); h_frame.pack(fill="x", pady=20)
        ctk.CTkLabel(h_frame, text="HOOBS & Homebridge Integration", font=FONT_H1, text_color=COLOR_TEXT).pack(side="left")
        self.hoobs_status_lbl = ctk.CTkLabel(h_frame, text="Checking...", font=("Arial", 12, "bold"), text_color="gray"); self.hoobs_status_lbl.pack(side="right", padx=10)

        c = ctk.CTkFrame(f, fg_color=COLOR_CARD); c.pack(fill="both", expand=True, padx=20, pady=10)
        
        ctk.CTkLabel(c, text="1. Configuration Generator", font=FONT_H2, text_color=COLOR_TEXT).pack(pady=(20, 5), anchor="w", padx=20)
        ctk.CTkLabel(c, text="Use this JSON block in your Homebridge/HOOBS 'config.json' file.", font=FONT_BODY, text_color=COLOR_SUBTEXT).pack(pady=0, anchor="w", padx=20)
        
        ctk.CTkButton(c, text="Scan & Generate Config", height=30, fg_color=COLOR_ACCENT, command=self.generate_hoobs_config).pack(pady=10, padx=20, anchor="w")
        self.hoobs_text = ctk.CTkTextbox(c, font=FONT_MONO, height=120); self.hoobs_text.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkButton(c, text="Copy Config", fg_color=COLOR_BTN_SECONDARY, text_color=COLOR_BTN_TEXT, width=100, command=lambda: pyperclip.copy(self.hoobs_text.get("1.0", "end"))).pack(pady=5, padx=20, anchor="e")

        self.install_frame = ctk.CTkFrame(c, fg_color="transparent"); self.install_frame.pack(fill="x", padx=20, pady=20)
        
        self.lbl_install_header = ctk.CTkLabel(self.install_frame, text="2. Software Installation", font=FONT_H2, text_color=COLOR_TEXT, anchor="w")
        
        # --- OS SPECIFIC INSTALLER UI ---
        if sys.platform == "darwin":
            # Mac UI (Manual Instructions)
            self.lbl_mac_install_desc = ctk.CTkLabel(self.install_frame, text="Homebridge is not running. macOS requires manual installation via Terminal.", font=FONT_BODY, text_color=COLOR_SUBTEXT, anchor="w")
            mac_inst = (
                "1. Install Node.js (v20 LTS)\n"
                "   - macOS 12+ (Monterey+): Run 'brew install node' or use the official installer.\n"
                "   - macOS 11 (Big Sur): Download the v20 .pkg from https://nodejs.org\n\n"
                "2. Open Terminal and run these commands one by one:\n"
                "   sudo npm install -g --unsafe-perm homebridge homebridge-config-ui-x\n"
                "   sudo npm install -g homebridge-wemo homebridge-alexa\n"
                "   mkdir -p ~/.homebridge\n"
                "   sudo hb-service install\n"
            )
            self.mac_install_text = ctk.CTkTextbox(self.install_frame, height=180, font=FONT_MONO, fg_color=COLOR_BG)
            self.mac_install_text.insert("1.0", mac_inst)
            self.mac_install_text.configure(state="disabled")
        else:
            # Windows / Linux UI (Auto-Installer Button)
            self.lbl_install_desc = ctk.CTkLabel(self.install_frame, text="Homebridge is not running. Install the compatibility layer to enable Alexa/Google Home.", font=FONT_BODY, text_color=COLOR_SUBTEXT, anchor="w")
            self.btn_install_hoobs = ctk.CTkButton(self.install_frame, text="Install Compatibility Layer", height=40, fg_color=COLOR_SUCCESS, command=self.run_hoobs_installer)
        
        # Connected UI
        self.lbl_connect_header = ctk.CTkLabel(self.install_frame, text="2. Connection & Setup", font=FONT_H2, text_color=COLOR_TEXT, anchor="w")
        self.lbl_connect_desc = ctk.CTkLabel(self.install_frame, text="Homebridge is running! Follow these steps to finish setup:", font=FONT_BODY, text_color=COLOR_SUBTEXT, anchor="w")
        self.connect_steps = ctk.CTkTextbox(self.install_frame, height=120, font=FONT_BODY, fg_color=COLOR_BG)
        self.connect_steps.insert("1.0", "1. Click 'Launch Dashboard' below.\n2. Login with admin / admin.\n3. Go to Plugins -> Search 'homebridge-wemo' -> Install.\n4. Go to Config -> Paste the JSON from above into 'platforms'.\n5. Restart Homebridge (Top Right Icon).")
        self.connect_steps.configure(state="disabled")
        self.btn_open_hoobs = ctk.CTkButton(self.install_frame, text="Launch Dashboard (localhost:8581)", height=40, fg_color=COLOR_ACCENT, command=lambda: webbrowser.open(HOOBS_URL))

        self._update_hoobs_ui(False)

    def _hoobs_monitor(self):
        last_state = None
        while self.monitoring:
            is_running = NetworkUtils.check_hoobs_status()
            self.hoobs_online = is_running
            if is_running != last_state:
                self.after(0, lambda: self._update_status_label(is_running))
                self.after(0, lambda: self._update_hoobs_ui(is_running))
                last_state = is_running
            time.sleep(5)

    def _update_status_label(self, is_running):
        if is_running: self.hoobs_status_lbl.configure(text="✅ SERVICE ONLINE", text_color=COLOR_SUCCESS)
        else: self.hoobs_status_lbl.configure(text="⚠️ SERVICE OFFLINE", text_color="orange")

    def _update_hoobs_ui(self, is_running):
        for widget in self.install_frame.winfo_children(): widget.pack_forget()
        
        if is_running:
            self.lbl_connect_header.pack(fill="x", pady=(0,5))
            self.lbl_connect_desc.pack(fill="x", pady=(0,10))
            self.connect_steps.pack(fill="x", pady=(0,10))
            self.btn_open_hoobs.pack(fill="x")
        else:
            self.lbl_install_header.pack(fill="x", pady=(0,5))
            if sys.platform == "darwin":
                self.lbl_mac_install_desc.pack(fill="x", pady=(0,5))
                self.mac_install_text.pack(fill="x", pady=(0,10))
            else:
                self.lbl_install_desc.pack(fill="x", pady=(0,10))
                self.btn_install_hoobs.pack(fill="x")

    def generate_hoobs_config(self):
        devices = []
        for dev in self.known_devices_map.values():
            try:
                mac = getattr(dev, 'mac', 'UNKNOWN').replace(':', '')
                devices.append(f'"{mac}"')
            except: pass
        json_str = "{\n    \"platform\": \"BelkinWeMo\",\n    \"name\": \"WeMo Platform\",\n    \"noMotionTimer\": 60,\n    \"discovery\": false,\n    \"manual_devices\": [\n        " + ",\n        ".join(devices) + "\n    ]\n}"
        self.hoobs_text.delete("1.0", "end"); self.hoobs_text.insert("1.0", json_str)

    def run_hoobs_installer(self):
        # This function is now only called on Windows / Linux
        target = None
        is_script = False
        
        # Determine executable path
        if sys.platform == "win32":
            target = os.path.join(BASE_DIR, "Wemo_HOOBS_Integration_Setup.exe")
        else:
            bin_name = "wemo_hoobs_setup"
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
                possible = os.path.join(base, bin_name)
                if os.path.exists(possible): target = possible
            if not target:
                script = os.path.join(BASE_DIR, "hoobs_installer.py")
                if os.path.exists(script): target = script; is_script = True
        
        if not target or not os.path.exists(target):
             if os.path.exists("wemo_hoobs_setup"): target = "wemo_hoobs_setup"
        
        if not target or not os.path.exists(target):
            messagebox.showerror("Error", "Installer component not found in application directory.")
            return
        
        # Execute Installer Securely
        try:
            if sys.platform == "win32":
                import ctypes
                if is_script: ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, target, None, 1)
                else: ctypes.windll.shell32.ShellExecuteW(None, "runas", target, "", None, 1)
            else:
                # Add quotes around the target path to handle spaces safely
                cmd = f'sudo python3 "{target}"' if is_script else f'sudo "{target}"'
                
                # Check for standard Linux terminal emulators and launch visible terminal
                term_launched = False
                
                # Wrap command to keep terminal open if there's an immediate failure
                bash_wrapper = f"{cmd}; echo; read -p 'Press Enter to exit...'"
                
                terminals = {
                    'ptyxis': ['--', 'bash', '-c'],          # Fedora 40+ GNOME
                    'kgx': ['-e', 'bash', '-c'],             # Fedora 39/GNOME Console
                    'gnome-terminal': ['--', 'bash', '-c'],  # Ubuntu/Older GNOME
                    'konsole': ['-e', 'bash', '-c'],         # KDE (Fedora Spin / Kubuntu)
                    'xfce4-terminal': ['-x', 'bash', '-c'],  # XFCE
                    'mate-terminal': ['-x', 'bash', '-c'],   # MATE
                    'lxterminal': ['-e', 'bash', '-c'],      # LXDE
                    'tilix': ['-e', 'bash', '-c'],           # Tilix
                    'terminator': ['-x', 'bash', '-c'],      # Terminator
                    'xterm': ['-e', 'bash', '-c']            # Universal Fallback
                }
                
                for term, flags in terminals.items():
                    if shutil.which(term):
                        try:
                            # Wrap command in bash to ensure sudo prompts function correctly in popup window
                            subprocess.Popen([term] + flags + [bash_wrapper])
                            term_launched = True
                            break
                        except Exception:
                            pass
                
                # Fallback if no known terminal emulator is found
                if not term_launched:
                    subprocess.Popen(cmd.split())
                    
        except Exception as e: messagebox.showerror("Launch Error", str(e))

    # --- STATE POLLER ---
    def _state_poller(self):
        while self.monitoring:
            for device in self.live_devices.copy().values():
                try:
                    state = device.get_state(force_update=True)
                except Exception:
                    state = None
                self.after(0, self._update_device_state, device, state)
            time.sleep(2)

    def _update_device_state(self, device, state):
        udn = device.udn
        if self.live_devices.get(udn) is not device:
            return
        if state is None:
            del self.live_devices[udn]
            self._refresh_device_views()
            self._save_device_cache()
            return
        record = self.device_cache[udn]
        changed = state != record["state"]
        record.update(state=state, last_seen=datetime.datetime.now().isoformat(timespec="seconds"))
        if changed or time.monotonic() - self.cache_written_at >= 60:
            self._save_device_cache()
        switch = self.device_switches.get(udn)
        if switch is not None:
            if state > 0:
                switch.select()
            else:
                switch.deselect()

    # --- PROVISIONER ---
    def create_provisioner(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["prov"] = f
        f.columnconfigure(0, weight=1); f.columnconfigure(1, weight=2); f.rowconfigure(0, weight=1)
        lc = ctk.CTkFrame(f, fg_color="transparent"); lc.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(lc, text="Step 1: Locate Device", font=FONT_H1, text_color=COLOR_TEXT).pack(anchor="w", pady=(0,5))
        sf = ctk.CTkFrame(lc, fg_color=COLOR_FRAME); sf.pack(fill="x", pady=(0, 20))
        self.btn_scan_setup = ctk.CTkButton(sf, text="Scan Airwaves", command=self.scan_ssids, fg_color=COLOR_ACCENT); self.btn_scan_setup.pack(pady=10, padx=10, fill="x")
        self.ssid_list = ctk.CTkScrollableFrame(sf, height=100, label_text="Nearby Networks", label_text_color=COLOR_TEXT); self.ssid_list.pack(fill="x", padx=10, pady=(0,10))
        ctk.CTkLabel(lc, text="Step 2: Configuration", font=FONT_H2, text_color=COLOR_TEXT).pack(anchor="w", pady=(0,5))
        inf = ctk.CTkFrame(lc, fg_color="transparent"); inf.pack(fill="x")
        pr = ctk.CTkFrame(inf, fg_color="transparent"); pr.pack(fill="x", pady=5)
        self.profile_combo = ctk.CTkComboBox(pr, values=["Select Saved Profile..."] + list(self.profiles.keys()), command=self.apply_profile, width=200); self.profile_combo.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(pr, text="Save", width=50, command=self.save_current_profile, fg_color=COLOR_ACCENT).pack(side="left", padx=5)
        ctk.CTkButton(pr, text="Del", width=50, fg_color=COLOR_DANGER, command=self.delete_profile).pack(side="left")
        self.name_entry = ctk.CTkEntry(inf, placeholder_text="Device Name"); self.name_entry.pack(fill="x", pady=5)
        self.ssid_entry = ctk.CTkEntry(inf, placeholder_text="SSID"); self.ssid_entry.pack(fill="x", pady=5)
        self.pass_entry = ctk.CTkEntry(inf, placeholder_text="Password", show="*"); self.pass_entry.pack(fill="x", pady=5)
        self.prov_btn = ctk.CTkButton(lc, text="Push Configuration", fg_color=COLOR_SUCCESS, height=50, state="disabled", command=self.run_provision_thread); self.prov_btn.pack(fill="x", pady=20)
        rc = ctk.CTkFrame(f, fg_color="transparent"); rc.grid(row=0, column=1, sticky="nsew")
        self.status_frame = ctk.CTkFrame(rc, fg_color=("#fadbd8", "#331111"), border_color="#ff5555", border_width=2); self.status_frame.pack(fill="x", pady=(0, 10))
        self.status_lbl_icon = ctk.CTkLabel(self.status_frame, text="X", font=("Arial", 30)); self.status_lbl_icon.pack(side="left", padx=15, pady=15)
        stxt = ctk.CTkFrame(self.status_frame, fg_color="transparent"); stxt.pack(side="left", fill="x")
        self.status_lbl_text = ctk.CTkLabel(stxt, text="NOT CONNECTED", font=FONT_H2, text_color="#ff5555"); self.status_lbl_text.pack(anchor="w")
        self.status_lbl_sub = ctk.CTkLabel(stxt, text="Connect Wi-Fi to 'Wemo.Mini.XXX'", font=FONT_BODY, text_color=COLOR_TEXT); self.status_lbl_sub.pack(anchor="w")
        self.override_link = ctk.CTkLabel(rc, text="[Manual Override]", font=("Arial", 10, "underline"), text_color="gray", cursor="hand2"); self.override_link.pack(anchor="e", pady=(0, 5)); self.override_link.bind("<Button-1>", lambda e: self.force_unlock())
        ctk.CTkLabel(rc, text="Live Operation Log", font=FONT_BODY, text_color=COLOR_TEXT).pack(anchor="w")
        self.prov_log = ctk.CTkTextbox(rc, font=FONT_MONO, activate_scrollbars=True); self.prov_log.pack(fill="both", expand=True)

    def run_provision_thread(self):
        ssid = self.ssid_entry.get(); pwd = self.pass_entry.get(); name = self.name_entry.get()
        if not ssid: return messagebox.showwarning("Missing Data", "Enter SSID.")
        self.prov_btn.configure(state="disabled", text="Running..."); self.prov_log.delete("1.0", "end")
        threading.Thread(target=self._provision_task, args=(ssid, pwd, name, self.current_setup_ip or "10.22.22.1", self.current_setup_port), daemon=True).start()

    def _provision_task(self, s, p, n, ip, pt):
        self.log_prov(f"--- Configuring {ip} ---")
        try:
            url = f"http://{ip}:{pt or 49153}/setup.xml"
            dev = pywemo.discovery.device_from_description(url)
            if n and hasattr(dev, 'basicevent'): dev.basicevent.ChangeFriendlyName(FriendlyName=n); time.sleep(1)
            for m in [2,1,0]:
                try: dev.setup(ssid=s, password=p, _encrypt_method=m); self.log_prov("SUCCESS! Rebooting..."); break
                except: pass
        except Exception as e: self.log_prov(f"Error: {e}")
        self.prov_btn.configure(state="normal", text="Push Configuration")

    def log_prov(self, m): self.prov_log.insert("end", f"{m}\n"); self.prov_log.see("end")
    
    def scan_ssids(self):
        for w in self.ssid_list.winfo_children(): w.destroy()
        threading.Thread(target=self._scan_thread_logic, args=(ctk.CTkLabel(self.ssid_list, text="Scanning...", text_color="yellow"),), daemon=True).start()
    
    def _scan_thread_logic(self, l):
        l.pack(); w = NetworkUtils.scan_wifi_networks(); l.destroy()
        if w: 
            for s in set(w): self.after(0, lambda x=s: self.build_ssid_card(x))
        else: self.after(0, lambda: ctk.CTkLabel(self.ssid_list, text="No Wemo networks found.", text_color="#ff5555").pack())
    
    def build_ssid_card(self, s):
        c = ctk.CTkFrame(self.ssid_list, fg_color=COLOR_FRAME); c.pack(fill="x", pady=2, padx=5)
        ctk.CTkLabel(c, text=s, font=("Arial", 12, "bold"), text_color=COLOR_TEXT).pack(side="left", padx=10)
        if WifiAutomator.can_automate(): 
            def connect_action(ssid=s):
                self.log_prov(f"Connecting to {ssid}...")
                threading.Thread(target=lambda: self._connect_task(ssid), daemon=True).start()
            ctk.CTkButton(c, text="Connect", width=80, height=24, fg_color=COLOR_SUCCESS, command=connect_action).pack(side="right", padx=10)
        else: ctk.CTkLabel(c, text="> Connect Manually", text_color="gray", font=("Arial", 10)).pack(side="right", padx=10)

    def _connect_task(self, ssid):
        success = WifiAutomator.connect_open_network(ssid)
        if success: self.log_prov(f"Success: Connected to {ssid}")
        else: self.log_prov(f"Failed to connect to {ssid}. Try manual connection.")

    def apply_profile(self, c):
        if c in self.profiles: self.ssid_entry.delete(0, "end"); self.ssid_entry.insert(0, c); self.pass_entry.delete(0, "end"); self.pass_entry.insert(0, self.profiles[c])
    def save_current_profile(self):
        s, p = self.ssid_entry.get(), self.pass_entry.get()
        if s and p: self.profiles[s] = p; self.save_json(PROFILE_FILE, self.profiles); self.profile_combo.configure(values=["Select Saved Profile..."] + list(self.profiles.keys())); self.profile_combo.set(s)
    def delete_profile(self):
        c = self.profile_combo.get()
        if c in self.profiles: del self.profiles[c]; self.save_json(PROFILE_FILE, self.profiles); self.profile_combo.configure(values=["Select Saved Profile..."] + list(self.profiles.keys())); self.profile_combo.set("Select Saved Profile...")

    def _connection_monitor(self):
        while self.monitoring:
            if self.manual_override_active: time.sleep(3); continue
            found = False
            for ip in ["10.22.22.1", "192.168.49.1"]:
                for p in [49153, 49152, 49154]:
                    try: 
                        d = pywemo.discovery.device_from_description(f"http://{ip}:{p}/setup.xml")
                        if d: self.current_setup_ip=ip; self.current_setup_port=p; self.after(0, lambda: self.set_status_connected(d, ip, p)); found=True; break
                    except: pass
                if found: break
            if not found: self.current_setup_ip=None; self.after(0, self.set_status_disconnected)
            time.sleep(5)

    def set_status_connected(self, d, i, p):
        self.status_frame.configure(fg_color=("#d0f0c0", "#1a331a"), border_color="#28a745"); self.status_lbl_icon.configure(text="OK"); self.status_lbl_text.configure(text="CONNECTED", text_color="#28a745"); self.status_lbl_sub.configure(text=f"Found: {d.name} ({i}:{p})", text_color=COLOR_TEXT); self.prov_btn.configure(state="normal", text="Push Configuration"); self.override_link.pack_forget()
    def set_status_disconnected(self):
        self.status_frame.configure(fg_color=("#fadbd8", "#331111"), border_color="#ff5555"); self.status_lbl_icon.configure(text="X"); self.status_lbl_text.configure(text="NOT CONNECTED", text_color="#ff5555"); self.status_lbl_sub.configure(text="Connect Wi-Fi to 'Wemo.Mini.XXX'", font=("Arial", 12), text_color=COLOR_TEXT); self.prov_btn.configure(state="disabled", text="Waiting for Connection..."); self.override_link.pack(anchor="e", pady=(0, 5))
    def force_unlock(self):
        self.status_frame.configure(fg_color=("#fcf3cf", "#332200"), border_color="#FFA500"); self.status_lbl_icon.configure(text="(!)"); self.status_lbl_text.configure(text="MANUAL OVERRIDE", text_color="#FFA500"); self.status_lbl_sub.configure(text="Forced Unlock. Assuming 10.22.22.1.", text_color=COLOR_TEXT); self.prov_btn.configure(state="normal", text="Push Configuration (Forced)"); self.current_setup_ip="10.22.22.1"; self.manual_override_active=True

    @staticmethod
    def keyboard_control(widget, action):
        # CustomTkinter paints its controls on a canvas. Put the outer
        # widget in Tk's focus order and bind the standard activation keys.
        Misc.configure(widget, takefocus=1)
        for key in ("<space>", "<Return>"):
            Misc.bind(widget, key, lambda _: (action(), "break")[1])

    def bind_automation_keys(self):
        def navigate(tab):
            self.show_tab("sched")
            self.rules_tabs.set(tab)

        def library():
            self.library_source.set("Managed by Wemo Central Ops")
            navigate("Rules Library")
            self.render_rule_library()

        def choose():
            navigate("Wemo Central Ops schedules")
            self.choose_schedule_devices()

        def save():
            if self.frames["sched"].winfo_ismapped() and self.rules_tabs.get() == "Wemo Central Ops schedules":
                if self.add_job():
                    library()

        def handle(event, action):
            if self.grab_current() is None:
                action()
            return "break"

        for key, action in (("F6", library), ("F7", choose), ("F8", save), ("F9", self.manage_schedules_by_keyboard)):
            self.bind_all(f"<{key}>", lambda event, action=action: handle(event, action), add="+")

    def schedule_keyboard_list(self, dialog, labels, multiple=False):
        listing = Listbox(dialog, selectmode="multiple" if multiple else "browse",
                          exportselection=False, activestyle="dotbox")
        listing.pack(fill="both", expand=True, padx=12, pady=8)
        for label in labels:
            listing.insert("end", label)
        if labels:
            listing.activate(0)
            if not multiple:
                listing.selection_set(0)

        def move(step):
            if listing.size():
                index = max(0, min(listing.size() - 1, listing.index("active") + step))
                listing.activate(index)
                listing.see(index)
                if not multiple:
                    listing.selection_clear(0, "end")
                    listing.selection_set(index)
            return "break"

        listing.bind("<Up>", lambda _: move(-1))
        listing.bind("<Down>", lambda _: move(1))
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.after_idle(listing.focus_force)
        return listing

    def manage_schedules_by_keyboard(self):
        self.show_tab("sched")
        self.rules_tabs.set("Wemo Central Ops schedules")
        jobs = [("Desktop app", job) for job in self.schedules] + [
            ("Background service", job) for job in self.server_schedules]
        dialog = ctk.CTkToplevel(self)
        dialog.title("Manage Wemo Central Ops schedules")
        dialog.geometry("640x340")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Use ↑/↓ to select a schedule, Return to edit, or Delete to remove.").pack(padx=12, pady=8)
        names = self.rule_device_names()
        listing = self.schedule_keyboard_list(dialog, [
            f"{job['action']} · {', '.join(names.get(target['device_id'], target['device']) for target in schedule_targets(job))}"
            f" · {job['value']} · {source}" for source, job in jobs
        ])

        def edit(_):
            if listing.curselection():
                source, job = jobs[listing.curselection()[0]]
                if source == "Desktop app" or self.schedule_server_status is not None:
                    dialog.destroy()
                    self.edit_job(source, job)
                    self.sched_val_entry.focus_set()
            return "break"

        def delete(_):
            if listing.curselection():
                index = listing.curselection()[0]
                source, job = jobs[index]
                if messagebox.askyesno("Delete schedule", f"Delete this schedule?\n\n{listing.get(index)}",
                                       parent=dialog):
                    if self.mutate_schedule(source, "DELETE", job["id"]):
                        dialog.destroy()
                        self.rules_tabs.set("Rules Library")
            return "break"

        listing.bind("<Return>", edit)
        listing.bind("<Delete>", delete)
        listing.bind("<BackSpace>", delete)

    def create_schedule_ui(self):
        frame = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["sched"] = frame
        head = ctk.CTkFrame(frame, fg_color="transparent"); head.pack(fill="x", pady=20)
        ctk.CTkLabel(head, text="Automation Scheduler", font=FONT_H1, text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkLabel(frame, text="F6 Library  ·  F7 Choose devices  ·  F8 Save schedule  ·  F9 Manage schedules",
                     anchor="w", text_color=COLOR_SUBTEXT).pack(fill="x", padx=5)
        self.sched_mode_lbl = ctk.CTkLabel(head, text="MODE: CHECKING...", font=("Arial", 12, "bold"), text_color="gray"); self.sched_mode_lbl.pack(side="right", padx=10)
        self.rules_tabs = ctk.CTkTabview(frame)
        self.rules_tabs.pack(fill="both", expand=True, pady=5)
        library = self.rules_tabs.add("Rules Library")
        self.create_rule_library(library)
        schedules = self.rules_tabs.add("Wemo Central Ops schedules")
        stored = self.rules_tabs.add("Stored on device")
        ctk.CTkLabel(schedules, text="Managed by Wemo Central Ops. Choose the desktop app or background service to run it.",
                     anchor="w").pack(fill="x", padx=5)
        loc_frame = ctk.CTkFrame(schedules, fg_color=COLOR_FRAME); loc_frame.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(loc_frame, text="Solar Location:", font=FONT_H2, text_color=COLOR_TEXT).pack(side="left", padx=10)
        self.loc_lbl = ctk.CTkLabel(loc_frame, text="Detecting...", text_color="orange"); self.loc_lbl.pack(side="left")
        ctk.CTkButton(loc_frame, text="Update Solar Data", width=120, fg_color=COLOR_BTN_SECONDARY, text_color=COLOR_BTN_TEXT, command=self.update_solar_data).pack(side="right", padx=10, pady=5)
        creator_frame = ctk.CTkFrame(schedules); creator_frame.pack(fill="x", pady=5)
        r1 = ctk.CTkFrame(creator_frame, fg_color="transparent"); r1.pack(fill="x", padx=10, pady=5)
        choose_devices = ctk.CTkButton(r1, text="Choose devices", width=125, command=self.choose_schedule_devices)
        choose_devices.pack(side="left", padx=5)
        self.keyboard_control(choose_devices, choose_devices.invoke)
        self.schedule_target_label = ctk.CTkLabel(r1, text="No devices selected", anchor="w")
        self.schedule_target_label.pack(side="left", padx=5)
        self.sched_action_combo = ctk.CTkComboBox(r1, values=["Turn ON", "Turn OFF", "Toggle"], width=100); self.sched_action_combo.pack(side="left", padx=5)
        self.sched_type_combo = ctk.CTkComboBox(r1, values=["Time (Fixed)", "Sunrise", "Sunset"], width=130, command=self.on_sched_type_change); self.sched_type_combo.pack(side="left", padx=5)
        target_panel = ctk.CTkFrame(creator_frame, fg_color="transparent")
        target_panel.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(target_panel, text="Devices controlled · click or press Space to select").pack(anchor="w")
        self.schedule_target_list = Listbox(target_panel, selectmode="multiple", exportselection=False,
                                            activestyle="dotbox", height=4)
        self.schedule_target_list.pack(fill="x")
        self.schedule_target_list.bind("<<ListboxSelect>>", self.update_inline_schedule_selection)
        self.schedule_target_list.bind("<space>", self.toggle_inline_schedule_device)
        r2 = ctk.CTkFrame(creator_frame, fg_color="transparent"); r2.pack(fill="x", padx=10, pady=5)
        self.sched_val_lbl = ctk.CTkLabel(r2, text="Time (HH:MM):", text_color=COLOR_TEXT); self.sched_val_lbl.pack(side="left", padx=5)
        self.sched_val_entry = ctk.CTkEntry(r2, width=80, placeholder_text="18:00"); self.sched_val_entry.pack(side="left", padx=5)
        self.sched_offset_combo = ctk.CTkComboBox(r2, values=["+ (After)", "- (Before)"], width=100); self.sched_offset_combo.pack(side="left", padx=5); self.sched_offset_combo.pack_forget()
        self.day_vars = []; days = ["M", "T", "W", "Th", "F", "Sa", "Su"]; day_frame = ctk.CTkFrame(r2, fg_color="transparent"); day_frame.pack(side="left", padx=20)
        for i, d in enumerate(days):
            v = ctk.BooleanVar(value=True)
            self.day_vars.append(v)
            day = ctk.CTkCheckBox(day_frame, text=d, variable=v, width=40, text_color=COLOR_TEXT)
            day.pack(side="left", padx=2)
            self.keyboard_control(day, day.toggle)
        self.save_job_button = ctk.CTkButton(r2, text="Create schedule", width=115, fg_color=COLOR_SUCCESS, command=self.add_job)
        self.save_job_button.pack(side="right", padx=10)
        self.keyboard_control(self.save_job_button, self.save_job_button.invoke)
        cancel_edit = ctk.CTkButton(r2, text="Cancel edit", width=85, command=self.cancel_job_edit)
        cancel_edit.pack(side="right")
        self.keyboard_control(cancel_edit, cancel_edit.invoke)
        controls = ctk.CTkFrame(schedules, fg_color="transparent")
        controls.pack(fill="x", pady=5)
        ctk.CTkLabel(controls, text="Runner:").pack(side="left", padx=4)
        self.schedule_runner = ctk.CTkComboBox(controls, values=["Desktop app", "Background service"], width=150, state="readonly")
        self.schedule_runner.set("Desktop app")
        self.schedule_runner.pack(side="left", padx=5)
        self.rule_filter = ctk.CTkComboBox(controls, values=["All devices"], width=180, state="readonly",
                                           command=lambda _: self.render_jobs())
        self.rule_filter.set("All devices")
        self.rule_filter.pack(side="left", padx=5)
        ctk.CTkButton(controls, text="Export", width=70, command=self.export_schedules).pack(side="right", padx=5)
        ctk.CTkButton(controls, text="Restore", width=70, command=self.import_schedules).pack(side="right", padx=5)
        self.job_list_frame = ctk.CTkScrollableFrame(schedules)
        self.job_list_frame.pack(fill="both", expand=True)
        ctk.CTkLabel(stored, text="Rules that control or are triggered by the selected Wemo.",
                     anchor="w").pack(fill="x", padx=5)
        native_controls = ctk.CTkFrame(stored, fg_color="transparent")
        native_controls.pack(fill="x", pady=5)
        self.stored_device = ctk.CTkComboBox(native_controls, values=[], width=200,
                                             state="readonly", command=self.reset_native_rules)
        self.stored_device.pack(side="left", padx=4)
        self.native_read = ctk.CTkButton(native_controls, text="Read device rules", command=self.read_device_rules)
        self.native_read.pack(side="left", padx=4)
        self.show_database_copies = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(native_controls, text="Show database copies", variable=self.show_database_copies,
                        command=self.refresh_device_rule_view).pack(side="left", padx=8)
        self.native_choices = ctk.CTkComboBox(stored, values=[], width=400, state="readonly",
                                             command=lambda _: self.update_native_buttons())
        self.native_choices.pack(fill="x", pady=4)
        actions = ctk.CTkFrame(stored, fg_color="transparent")
        actions.pack(fill="x", pady=4)
        self.native_create = ctk.CTkButton(actions, text="Create on device", command=lambda: self.open_native_editor())
        self.native_edit = ctk.CTkButton(actions, text="Edit on device", command=lambda: self.open_native_editor(self.selected_native_rule()))
        self.native_delete = ctk.CTkButton(actions, text="Delete from device", command=self.delete_native_rule)
        self.native_auto_off = ctk.CTkButton(actions, text="Configure auto-off", command=self.configure_native_auto_off)
        for button in (self.native_create, self.native_edit, self.native_delete, self.native_auto_off):
            button.configure(state="disabled")
            button.pack(side="left", padx=4)
        self.stored_status = ctk.CTkLabel(stored, text="", anchor="w", justify="left", wraplength=720)
        self.stored_status.pack(fill="x", padx=5)
        self.stored_rules = ctk.CTkScrollableFrame(stored)
        self.stored_rules.pack(fill="both", expand=True)
        self.set_stored_rules("Select a discovered Wemo to inspect its stored rules.")
        self.render_jobs()
        self.update_solar_data()

    def create_rule_library(self, parent):
        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", pady=5)
        self.library_device = ctk.CTkComboBox(controls, values=["All devices"], width=170,
                                              state="readonly", command=lambda _: self.render_rule_library())
        self.library_device.set("All devices")
        self.library_device.pack(side="left", padx=4)
        self.library_source = ctk.CTkComboBox(controls, values=["All sources", "Stored on device", "Managed by Wemo Central Ops"],
                                              width=170, state="readonly", command=lambda _: self.render_rule_library())
        self.library_source.set("All sources")
        self.library_source.pack(side="left", padx=4)
        ctk.CTkButton(controls, text="Refresh rules", width=95, command=self.refresh_rule_inventory).pack(side="left", padx=4)
        create = ctk.CTkFrame(parent, fg_color="transparent")
        create.pack(fill="x")
        ctk.CTkButton(create, text="New Wemo Central Ops schedule", command=self.new_managed_schedule).pack(side="left", padx=4)
        ctk.CTkButton(create, text="New device rule", command=self.open_device_rule_source).pack(side="left", padx=4)
        self.library_status = ctk.CTkLabel(parent, text="", anchor="w")
        self.library_status.pack(fill="x", padx=5)
        self.library_cards = ctk.CTkScrollableFrame(parent)
        self.library_cards.pack(fill="both", expand=True)

    def open_device_rule_source(self):
        self.rules_tabs.set("Stored on device")
        selected = self.library_device.get()
        if selected in self.known_devices_map:
            self.stored_device.set(selected)
            self.reset_native_rules()
            self.read_device_rules()

    def periodic_rule_refresh(self):
        self.refresh_rule_inventory()
        self.after(300000, self.periodic_rule_refresh)

    def refresh_rule_inventory(self):
        devices = []
        for udn, device in self.live_devices.items():
            if (hasattr(device, "rules") and udn not in self.inventory_pending
                    and not (self.native_busy and self.native_task_udn == udn)):
                marker = object()
                self.inventory_pending[udn] = marker
                devices.append((device, marker))
        self.render_rule_library()
        if not devices:
            return

        def read_all():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                pending = {pool.submit(read_device_rules, device): (device.udn, marker)
                           for device, marker in devices}
                for future in concurrent.futures.as_completed(pending):
                    udn, marker = pending[future]
                    try:
                        result = future.result()
                    except Exception:
                        result = None
                    self.after(0, self.finish_inventory_read, udn, marker, result)

        threading.Thread(target=read_all, daemon=True).start()

    def finish_inventory_read(self, udn, marker, result):
        if self.inventory_pending.get(udn) is not marker:
            return
        self.inventory_pending.pop(udn)
        if result is None:
            self.inventory_errors.add(udn)
        else:
            self.remember_device_rules(udn, result)
        self.render_rule_library()

    def remember_device_rules(self, udn, result):
        token, rules = result
        self.rule_inventory[udn] = {
            "token": token, "rules": rules,
            "checked": datetime.datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
        self.inventory_errors.discard(udn)
        self.persist_rule_inventory()

    def persist_rule_inventory(self):
        try:
            save_library(RULE_LIBRARY_FILE, {"devices": self.rule_inventory, "service": self.server_schedules})
            self.inventory_save_error = False
        except OSError:
            self.inventory_save_error = True

    def rule_device_names(self):
        names = {udn: item["name"] for udn, item in self.device_cache.items()}
        names.update({udn: device.name for udn, device in self.live_devices.items()})
        return names

    def render_rule_library(self):
        names = self.rule_device_names()
        options = set(names.values())
        options.update(target["device"] for job in self.schedules + self.server_schedules
                       for target in schedule_targets(job))
        self.library_device.configure(values=["All devices", *sorted(options)])
        selected = self.library_device.get()
        source_filter = self.library_source.get()
        status = f"{len(self.rule_inventory)} device inventories saved"
        if self.inventory_pending:
            status += f" · Reading {len(self.inventory_pending)} devices"
        if self.inventory_errors:
            status += f" · {len(self.inventory_errors)} reads failed"
        if self.inventory_save_error:
            status += " · Could not save library"
        self.library_status.configure(text=status)
        for widget in self.library_cards.winfo_children():
            widget.destroy()
        visible = 0
        if source_filter != "Managed by Wemo Central Ops":
            for group in group_native_rules(self.rule_inventory):
                devices = controlled_devices(group["rule"])
                if selected == "All devices" or selected in {names.get(udn, udn) for udn in devices}:
                    self.render_native_card(self.library_cards, group, names)
                    visible += 1
        if source_filter != "Stored on device":
            for runner, jobs in (("Desktop app", self.schedules), ("Background service", self.server_schedules)):
                for job in jobs:
                    targets = schedule_targets(job)
                    labels = [names.get(target["device_id"], target["device"]) for target in targets]
                    if selected == "All devices" or selected in labels or any(
                            selected == target["device"] for target in targets):
                        self.render_managed_card(runner, job, ", ".join(labels))
                        visible += 1
        if not visible:
            ctk.CTkLabel(self.library_cards, text="No rules in this view.").pack(pady=12)

    def rule_card(self, parent, title, source):
        card = ctk.CTkFrame(parent, fg_color=COLOR_FRAME)
        card.pack(fill="x", pady=4, padx=4)
        ctk.CTkLabel(card, text=title, font=FONT_H2, anchor="w").pack(fill="x", padx=10, pady=(7, 0))
        ctk.CTkLabel(card, text=source, anchor="w", text_color=COLOR_SUBTEXT).pack(fill="x", padx=10)
        return card

    def rule_card_line(self, card, text):
        ctk.CTkLabel(card, text=text, justify="left", anchor="w", wraplength=660).pack(fill="x", padx=10)

    def render_native_card(self, parent, group, names, allow_edit=True):
        rule = group["rule"]
        status = {"1": "Enabled", "0": "Disabled"}.get(rule["state"], "Device-defined state")
        kind = {INTERVAL: "Fixed schedule", COUNTDOWN: "Auto-off countdown"}.get(rule["kind"], rule["kind"])
        card = self.rule_card(parent, rule["name"], f"Stored on device · {kind} · {status}")
        controls = sorted(names.get(udn, udn) for udn in controlled_devices(rule))
        self.rule_card_line(card, "Controls: " + (", ".join(controls) or "Unknown"))
        triggers = sorted(names.get(udn, udn) for udn in triggering_devices(rule))
        if triggers:
            self.rule_card_line(card, "Triggered by: " + ", ".join(triggers))
        timings = describe_native_rule(rule, names)
        for timing in timings[:2]:
            self.rule_card_line(card, timing)
        if group["conflict"]:
            self.rule_card_line(card, "Copies differ across devices")
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=10, pady=6)
        ctk.CTkButton(buttons, text="Details", width=70,
                      command=lambda: self.show_native_details(group, names)).pack(side="left")
        source = self.native_edit_source(group)
        if allow_edit and source:
            ctk.CTkButton(buttons, text="Edit timing", width=90,
                          command=lambda: self.edit_library_native(group)).pack(side="left", padx=6)
        if allow_edit and not group["conflict"] and rule.get("copyable"):
            ctk.CTkButton(buttons, text="Copy to Wemo Central Ops", command=lambda: self.copy_native_schedule(rule)
                          ).pack(side="left", padx=6)
        self.rule_card_line(card, "Device assignments are locked for native rules.")

    def copy_native_schedule(self, rule):
        schedules = paused_schedule_copies(rule, self.rule_device_names())
        if not schedules or not messagebox.askyesno(
                "Copy as paused schedules",
                f"Create {len(schedules)} paused desktop schedules from {rule['name']}? The native rule stays active if enabled."):
            return
        try:
            added = self.schedule_store.add(schedules, skip_duplicates=True)
            self.schedules = self.schedule_store.read()
            self.library_source.set("Managed by Wemo Central Ops")
            self.render_jobs()
            if len(added) != len(schedules):
                messagebox.showinfo("Copy complete", f"Added {len(added)} paused schedules. Matching schedules were already present.")
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not copy rule", str(error))

    def native_edit_source(self, group):
        if group["conflict"]:
            return None
        for udn, rule in group["copies"].items():
            device = self.live_devices.get(udn)
            if (rule["edit"] and device is not None and self.known_devices_map.get(device.name) is device
                    and hasattr(getattr(device, "rules", None), "StoreRules")
                    and udn not in self.inventory_errors):
                return device
        return None

    def edit_library_native(self, group):
        if self.native_busy:
            return
        device = self.native_edit_source(group)
        if device is None:
            return
        udn = device.udn
        report = self.rule_inventory.get(udn)
        if report is None or group["copies"][udn] not in report["rules"]:
            self.render_rule_library()
            return
        self.rules_tabs.set("Stored on device")
        self.stored_device.set(device.name)
        self.display_device_rules(device.name, udn, (report["token"], report["rules"]))
        self.open_native_editor(group["copies"][udn])

    def show_native_details(self, group, names):
        dialog = ctk.CTkToplevel(self)
        dialog.title(group["rule"]["name"])
        dialog.geometry("640x400")
        dialog.transient(self)
        details = ctk.CTkScrollableFrame(dialog)
        details.pack(fill="both", expand=True, padx=10, pady=10)
        for line in describe_native_rule(group["rule"], names):
            self.rule_card_line(details, line)
        self.rule_card_line(details, "Found on · last successful reads")
        for udn in group["copies"]:
            checked = self.rule_inventory.get(udn, {}).get("checked", "Unknown")
            state = "Offline" if udn not in self.live_devices else (
                "Last refresh failed" if udn in self.inventory_errors else "Online")
            self.rule_card_line(details, f"{names.get(udn, udn)} · {state} · {checked}")

    def render_managed_card(self, runner, job, name):
        status = "Enabled" if job.get("enabled", True) else "Paused"
        if runner == "Background service" and self.schedule_server_status is None:
            status += " · Service unavailable"
        card = self.rule_card(self.library_cards, job.get("name") or f"{job['action']} {name}",
                              f"Managed by Wemo Central Ops · {runner} · {status}")
        days = job["days"]
        weekday = "Every day" if len(days) == 7 else ", ".join(DAYS[(day + 1) % 7] for day in days)
        trigger = job["value"] if job["type"] == TRIGGERS[0] else (
            f"{job['value']} min {'before' if job.get('offset_dir') == -1 else 'after'} {job['type'].lower()}")
        self.rule_card_line(card, f"Controls: {name} · {weekday} · {trigger}")
        self.rule_card_line(card, f"Last run: {job.get('last_result') or job.get('last_run') or 'No recorded run'}")
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(anchor="w", padx=10, pady=6)
        enabled = job.get("enabled", True)
        state = "normal" if runner == "Desktop app" or self.schedule_server_status is not None else "disabled"

        def delete():
            if messagebox.askyesno("Delete schedule", f"Delete the {job['action']} schedule for {name}?"):
                self.mutate_schedule(runner, "DELETE", job["id"])

        for label, command in (
            ("Edit", lambda: self.edit_job(runner, job)),
            ("Pause" if enabled else "Resume", lambda: self.mutate_schedule(
                runner, "PATCH", job["id"], {"enabled": not enabled})),
            ("Delete", delete),
        ):
            ctk.CTkButton(actions, text=label, width=70, state=state, command=command).pack(side="left", padx=(0, 6))

    def on_sched_type_change(self, choice):
        if choice == "Time (Fixed)": self.sched_val_lbl.configure(text="Time (HH:MM):"); self.sched_val_entry.configure(placeholder_text="18:00"); self.sched_offset_combo.pack_forget()
        else: self.sched_val_lbl.configure(text="Offset (Mins):"); self.sched_val_entry.configure(placeholder_text="30"); self.sched_offset_combo.pack(side="left", padx=5, after=self.sched_val_entry)

    def update_solar_data(self):
        def task():
            solar_data = self.solar.get_solar_times()
            if solar_data:
                txt = f"Lat: {self.solar.lat} | Rise: {solar_data['sunrise']} | Set: {solar_data['sunset']}"
                self.after(0, lambda: self.loc_lbl.configure(text=txt, text_color="gray"))
                self.settings["lat"] = self.solar.lat; self.settings["lng"] = self.solar.lng; self.save_json(SETTINGS_FILE, self.settings)
            else: self.after(0, lambda: self.loc_lbl.configure(text="Location Failed.", text_color="red"))
        threading.Thread(target=task, daemon=True).start()

    def update_schedule_dropdown(self):
        self.refresh_inline_schedule_devices()
        self.render_jobs()

    def new_managed_schedule(self):
        self.cancel_job_edit()
        self.sched_action_combo.set("Turn ON")
        self.sched_type_combo.set(TRIGGERS[0])
        self.on_sched_type_change(TRIGGERS[0])
        self.sched_val_entry.delete(0, "end")
        for variable in self.day_vars:
            variable.set(True)
        device = self.known_devices_map.get(self.library_device.get())
        targets = [{"device": device.name, "device_id": device.udn}] if device else []
        self.set_schedule_targets(targets)
        self.rules_tabs.set("Wemo Central Ops schedules")

    def schedule_device_options(self):
        selected = {target["device_id"] or target["device"]: target for target in self.selected_schedule_targets}
        options = dict(selected)
        for device in self.live_devices.values():
            options[device.udn] = {"device": device.name, "device_id": device.udn}
            if device.name in selected and not selected[device.name]["device_id"]:
                selected[device.udn] = selected.pop(device.name)
                options.pop(device.name, None)
        return sorted(options.items(), key=lambda item: item[1]["device"].casefold()), selected

    def refresh_inline_schedule_devices(self):
        self.inline_schedule_options, selected = self.schedule_device_options()
        listing = self.schedule_target_list
        listing.delete(0, "end")
        for index, (key, target) in enumerate(self.inline_schedule_options):
            listing.insert("end", target["device"] + (" (offline)" if key not in self.live_devices else ""))
            if key in selected:
                listing.selection_set(index)
        self.update_inline_schedule_selection()

    def update_inline_schedule_selection(self, _=None):
        self.selected_schedule_targets = [
            self.inline_schedule_options[index][1] for index in self.schedule_target_list.curselection()]
        self.update_schedule_target_label()

    def toggle_inline_schedule_device(self, _):
        listing = self.schedule_target_list
        if listing.size():
            index = listing.index("active")
            if listing.selection_includes(index):
                listing.selection_clear(index)
            else:
                listing.selection_set(index)
            self.update_inline_schedule_selection()
        return "break"

    def set_schedule_targets(self, targets):
        self.selected_schedule_targets = targets
        self.refresh_inline_schedule_devices()

    def update_schedule_target_label(self):
        targets = self.selected_schedule_targets
        count = len(targets)
        text = targets[0]["device"] if count == 1 else f"{count} devices selected" if count else "No devices selected"
        self.schedule_target_label.configure(text=text)

    def choose_schedule_devices(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Choose devices for this schedule")
        dialog.geometry("420x420")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Use ↑/↓ to move, Space to select, and Return to apply.", wraplength=390).pack(pady=10)
        targets, selected = self.schedule_device_options()
        listing = self.schedule_keyboard_list(dialog, [
            target["device"] + (" (offline)" if key not in self.live_devices else "")
            for key, target in targets
        ], multiple=True)
        for index, (key, _) in enumerate(targets):
            if key in selected:
                listing.selection_set(index)
        if not targets:
            ctk.CTkLabel(dialog, text="Scan for Wemos from the dashboard first.").pack(pady=5)

        def toggle(_):
            if listing.size():
                index = listing.index("active")
                if listing.selection_includes(index):
                    listing.selection_clear(index)
                else:
                    listing.selection_set(index)
            return "break"

        def apply(_=None):
            self.set_schedule_targets([targets[index][1] for index in listing.curselection()])
            dialog.destroy()
            self.sched_val_entry.focus_force()
            return "break"

        listing.bind("<space>", toggle)
        listing.bind("<Return>", apply)
        ctk.CTkButton(dialog, text="Use selected devices", command=apply).pack(pady=10)

    def show_device_rules(self, device):
        self.show_tab("sched")
        self.rule_filter.set(device.name)
        self.stored_device.set(device.name)
        self.reset_native_rules(device.name)
        self.rules_tabs.set("Rules Library")
        if self.editing_schedule is None:
            self.set_schedule_targets([{"device": device.name, "device_id": device.udn}])
        self.library_device.set(device.name)
        self.render_jobs()

    def set_stored_rules(self, text):
        self.stored_status.configure(text=text)
        for widget in self.stored_rules.winfo_children():
            widget.destroy()

    def reset_native_rules(self, _=None):
        self.native_snapshot = None
        self.native_choices.configure(values=[])
        self.native_choices.set("")
        self.set_stored_rules("Click Read device rules to fetch the current rules.")
        self.update_native_buttons()

    def selected_native_rule(self):
        if self.native_snapshot is None:
            return None
        selection = self.native_choices.get()
        return next((rule for rule in self.native_snapshot[2]
                     if selection == self.native_rule_label(rule)), None)

    @staticmethod
    def native_rule_label(rule):
        return f"#{rule['id']} · {rule['name']}" + ("" if rule["edit"] else " (view only)")

    def update_native_buttons(self):
        snapshot = self.native_snapshot
        device = self.known_devices_map.get(self.stored_device.get())
        can_write = (not self.native_busy and snapshot is not None and device is not None
                     and device.udn == snapshot[0] and hasattr(getattr(device, "rules", None), "StoreRules"))
        selection = self.selected_native_rule()
        self.native_read.configure(state="disabled" if self.native_busy else "normal")
        self.native_create.configure(state="normal" if can_write else "disabled")
        self.native_auto_off.configure(state="normal" if can_write else "disabled")
        for button in (self.native_edit, self.native_delete):
            button.configure(state="normal" if can_write and selection and selection["edit"] else "disabled")

    def read_device_rules(self):
        self.run_native_rule_task()

    def run_native_rule_task(self, write=False, rule_id=None, values=None):
        if self.native_busy:
            return
        name = self.stored_device.get()
        device = self.known_devices_map.get(name)
        if device is None or not hasattr(device, "rules"):
            self.reset_native_rules()
            self.set_stored_rules("Select a reachable Wemo with the Rules service.")
            return
        snapshot = self.native_snapshot
        if write and (snapshot is None or snapshot[0] != device.udn):
            self.set_stored_rules("Read the selected device again before editing.")
            return
        self.native_busy = True
        self.native_task_udn = device.udn
        self.inventory_pending.pop(device.udn, None)
        self.reset_native_rules()
        self.set_stored_rules(f"{'Updating' if write else 'Reading'} {name}…")

        def run():
            try:
                if write:
                    result = write_device_rule(device, snapshot[1], rule_id, values,
                                               Path(APP_DATA_DIR) / "native-rule-backups")
                else:
                    result = read_device_rules(device)
                self.after(0, self.finish_native_rule_task, name, device.udn, result, None, write)
            except Exception as error:
                self.after(0, self.finish_native_rule_task, name, device.udn, None, str(error), write)

        threading.Thread(target=run, daemon=True).start()

    def finish_native_rule_task(self, name, udn, result, error, write):
        self.native_busy = False
        if error is None:
            self.remember_device_rules(udn, result)
        else:
            self.inventory_errors.add(udn)
        self.render_rule_library()
        if self.stored_device.get() != name:
            self.update_native_buttons()
            return
        if error is not None:
            self.set_stored_rules(f"{name}: {error}")
            self.update_native_buttons()
            return
        self.display_device_rules(name, udn, result, write)

    def refresh_device_rule_view(self):
        if self.native_snapshot is not None:
            udn, token, rules = self.native_snapshot
            self.display_device_rules(self.stored_device.get(), udn, (token, rules))

    def display_device_rules(self, name, udn, result, write=False):
        token, rules = result
        self.native_snapshot = (udn, tuple(token), rules)
        copies = self.show_database_copies.get()
        heading = f"Database copies reported by {name}" if copies else f"Rules that apply to {name}"
        self.set_stored_rules(heading + (" · Update verified" if write else ""))
        names = self.rule_device_names()
        visible = [rule for rule in rules if copies or udn in controlled_devices(rule) | triggering_devices(rule)]
        for rule in visible:
            self.render_native_card(self.stored_rules, {"rule": rule, "copies": {udn: rule}, "conflict": False},
                                    names, allow_edit=False)
        if not visible:
            self.stored_status.configure(text=f"No {'database copies' if copies else 'applicable rules'} for {name}.")
        options = [self.native_rule_label(rule) for rule in visible]
        self.native_choices.configure(values=options)
        self.native_choices.set(options[0] if options else "")
        self.update_native_buttons()

    def delete_native_rule(self):
        rule = self.selected_native_rule()
        if rule and rule["edit"] and messagebox.askyesno(
                "Delete device rule", f"Delete {rule['name']} from {self.stored_device.get()}?"):
            self.run_native_rule_task(write=True, rule_id=rule["id"])

    def configure_native_auto_off(self):
        if self.native_snapshot is None:
            return
        udn, _, rules = self.native_snapshot
        timers = [rule for rule in rules if rule["kind"] == COUNTDOWN
                  and (udn in rule["targets"] or any(entry[0] == udn for entry in rule["entries"]))]
        selected = self.selected_native_rule()
        if selected in timers:
            timer = selected
        elif len(timers) == 1:
            timer = timers[0]
        elif timers:
            messagebox.showinfo("Select an auto-off timer", "Choose an existing countdown in the rule list.")
            return
        else:
            self.open_native_editor(default_kind=COUNTDOWN)
            return
        if timer["edit"] is None:
            messagebox.showinfo("Auto-off timer", "This timer uses a shared or unsupported rule format.")
            return
        self.open_native_editor(timer)

    def open_native_editor(self, rule=None, default_kind=INTERVAL):
        original = rule["edit"] if rule else None
        name = self.stored_device.get()
        snapshot = self.native_snapshot
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"{'Edit' if rule else 'Create'} device rule")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=f"Store directly on {name}", font=FONT_H2).pack(padx=16, pady=8)
        rule_types = {"Fixed on/off schedule": INTERVAL, "Auto-off countdown": COUNTDOWN}
        initial_kind = original["kind"] if original else default_kind
        kind = ctk.CTkComboBox(dialog, values=list(rule_types), state="readonly")
        kind.set(next(label for label, value in rule_types.items() if value == initial_kind))
        if rule:
            kind.configure(state="disabled")
        kind.pack(pady=4)
        timing_help = ctk.CTkLabel(dialog, text="", wraplength=365)
        timing_help.pack(padx=16, pady=4)
        fields = {}
        default_name = f"{name} Auto-off"[:64] if initial_kind == COUNTDOWN else ""
        for key, label, default in (("name", "Rule name", default_name), ("start", "On time (HH:MM)", "18:00"),
                                    ("end", "Off time (HH:MM)", "23:00"), ("minutes", "Auto-off delay (minutes)", 5)):
            row = ctk.CTkFrame(dialog, fg_color="transparent")
            ctk.CTkLabel(row, text=label, width=165, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=150)
            entry.insert(0, str(original[key] if original else default))
            entry.pack(side="right")
            fields[key] = (row, entry)
        fields["name"][0].pack(padx=16, pady=4)

        def show_timing(value):
            countdown = rule_types[value] == COUNTDOWN
            timing_help.configure(text="Turn off after the device has been on for this many minutes."
                                  if countdown else "Turn on and off at the times set below.")
            for key in ("start", "end", "minutes"):
                fields[key][0].pack_forget()
            for key in (("minutes",) if countdown else ("start", "end")):
                fields[key][0].pack(padx=16, pady=4, before=day_row)

        day_row = ctk.CTkFrame(dialog, fg_color="transparent")
        day_row.pack(padx=16, pady=4)
        days = [ctk.BooleanVar(value=original is None or index in original["days"]) for index in range(1, 8)]
        for day, checked in zip(DAYS, days):
            ctk.CTkCheckBox(day_row, text=day, variable=checked, width=48).pack(side="left")
        enabled = ctk.BooleanVar(value=original["enabled"] if original else False)
        ctk.CTkCheckBox(dialog, text="Enable on device", variable=enabled).pack(pady=6)
        kind.configure(command=show_timing)
        show_timing(kind.get())

        def save():
            if snapshot is not self.native_snapshot or name != self.stored_device.get():
                messagebox.showerror("Read rules again", "The device selection changed.", parent=dialog)
                return
            values = {key: entry.get() for key, (_, entry) in fields.items()}
            values.update(kind=rule_types[kind.get()], enabled=enabled.get(),
                          days=[index for index, checked in enumerate(days, 1) if checked.get()])
            try:
                validate_rule(values)
            except ValueError as error:
                messagebox.showerror("Check rule details", str(error), parent=dialog)
                return
            dialog.destroy()
            self.run_native_rule_task(write=True, rule_id=rule["id"] if rule else None, values=values)

        ctk.CTkButton(dialog, text="Save to device", command=save).pack(pady=(4, 12))

    def cancel_job_edit(self):
        self.editing_schedule = None
        self.save_job_button.configure(text="Create schedule")

    def edit_job(self, source, job):
        self.rules_tabs.set("Wemo Central Ops schedules")
        self.editing_schedule = (source, job["id"])
        names = self.rule_device_names()
        self.set_schedule_targets([dict(target, device=names.get(target["device_id"], target["device"]))
                                   for target in schedule_targets(job)])
        self.sched_action_combo.set(job["action"])
        self.sched_type_combo.set(job["type"])
        self.on_sched_type_change(job["type"])
        self.sched_val_entry.delete(0, "end")
        self.sched_val_entry.insert(0, str(job["value"]))
        self.schedule_target_list.focus_set()
        self.sched_offset_combo.set("- (Before)" if job.get("offset_dir") == -1 else "+ (After)")
        for index, variable in enumerate(self.day_vars):
            variable.set(index in job["days"])
        self.schedule_runner.set(source)
        self.save_job_button.configure(text="Save edit")

    def mutate_schedule(self, source, method, job_id=None, fields=None):
        try:
            if source == "Desktop app":
                if method == "POST":
                    self.schedule_store.add([fields])
                else:
                    self.schedule_store.change(job_id, fields)
                self.schedules = self.schedule_store.read()
            else:
                if self.schedule_server_status is None:
                    raise ValueError("Connect to an updated background service first.")
                if fields and len(fields.get("targets", [])) > 1 and not self.schedule_server_status.get("multi_device_schedules"):
                    raise ValueError("Update the background service to save a schedule for multiple devices.")
                response = requests.request(method, f"{SERVER_URL}/api/schedules",
                                            params={"id": job_id}, json=fields, timeout=2)
                response.raise_for_status()
                self.server_schedules = self.api.get_schedules()
                self.persist_rule_inventory()
            self.render_jobs()
            return True
        except (ValueError, OSError, StopIteration, requests.RequestException) as error:
            messagebox.showerror("Schedule could not be saved", str(error))
            return False

    def add_job(self):
        try:
            source, job_id = self.editing_schedule or (self.schedule_runner.get(), None)
            jobs = self.schedules if source == "Desktop app" else self.server_schedules
            previous = next((job for job in jobs if job["id"] == job_id), {})
            fields = schedule_fields({
                "targets": self.selected_schedule_targets,
                "action": self.sched_action_combo.get(), "type": self.sched_type_combo.get(),
                "value": self.sched_val_entry.get(), "days": [i for i, v in enumerate(self.day_vars) if v.get()],
                "offset_dir": -1 if self.sched_offset_combo.get() == "- (Before)" else 1,
                "enabled": previous.get("enabled", True),
            })
            if self.mutate_schedule(source, "PATCH" if job_id is not None else "POST", job_id, fields):
                self.cancel_job_edit()
                return True
        except ValueError as error:
            messagebox.showwarning("Invalid schedule", str(error))
        return False

    def render_jobs(self):
        self.render_rule_library()
        for widget in self.job_list_frame.winfo_children():
            widget.destroy()
        all_jobs = [("Desktop app", job) for job in self.schedules] + [
            ("Background service", job) for job in self.server_schedules]
        names = set(self.known_devices_map)
        names.update(target["device"] for _, job in all_jobs for target in schedule_targets(job))
        device_names = self.rule_device_names()
        self.rule_filter.configure(values=["All devices", *sorted(names)])
        self.stored_device.configure(values=sorted(self.known_devices_map))
        selected = self.rule_filter.get()
        visible = 0
        for source, job in all_jobs:
            targets = schedule_targets(job)
            labels = [device_names.get(target["device_id"], target["device"]) for target in targets]
            if selected != "All devices" and selected not in labels and all(
                    selected != target["device"] for target in targets):
                continue
            online = all(find_device(target, {**self.known_devices_map, **self.live_devices}) is not None
                         for target in targets)
            visible += 1
            self.render_job(source, job, ", ".join(labels), online)
        if not visible:
            ctk.CTkLabel(self.job_list_frame, text="No schedules for this selection.").pack(pady=15)

    def render_job(self, source, job, name, online):
        enabled = job.get("enabled", True)
        service = source == "Background service"
        ready = not service or self.schedule_server_status is not None
        today = datetime.date.today()
        solar = self.solar.solar_times if self.solar.last_fetch == today else None
        if service:
            solar = (self.schedule_server_status or {}).get("solar", {})
            if solar.get("date") != today.isoformat():
                solar = None
        upcoming = next_run(job, datetime.datetime.now().replace(second=0, microsecond=0),
                            solar, estimate_future_solar=True)
        next_label = upcoming.strftime("%a %b %d, %H:%M") if upcoming else "Pending trigger data"
        if upcoming and job["type"] in TRIGGERS[1:] and upcoming.date() != today:
            next_label += " (estimated)"
        if not enabled:
            next_label = "Paused"
        elif not ready:
            next_label = "No verified runner"
        elif not online:
            next_label += " · One or more devices unavailable to app"
        if not service:
            source += " (while open)"
        days = ", ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[i] for i in job["days"])
        if job["type"] == TRIGGERS[0]:
            trigger = job["value"]
        else:
            direction = "before" if job.get("offset_dir") == -1 else "after"
            trigger = f"{job['value']} min {direction} {job['type'].lower()}"
        row = ctk.CTkFrame(self.job_list_frame, fg_color=COLOR_FRAME)
        row.pack(fill="x", pady=3)
        text = (f"{name} · {job['action']} · {trigger} · {days}\n"
                f"{source} | Next: {next_label}\n"
                f"Last: {job.get('last_result') or job.get('last_run') or 'No recorded run'}")
        ctk.CTkLabel(row, text=text, justify="left", anchor="w", wraplength=530).pack(side="left", padx=8, pady=5)
        runner = "Background service" if service else "Desktop app"
        state = "normal" if ready else "disabled"
        for label, command in (
            ("Delete", lambda: self.mutate_schedule(runner, "DELETE", job["id"])),
            ("Pause" if enabled else "Resume", lambda: self.mutate_schedule(runner, "PATCH", job["id"], {"enabled": not enabled})),
            ("Edit", lambda: self.edit_job(runner, job)),
        ):
            ctk.CTkButton(row, text=label, width=55, state=state, command=command).pack(side="right", padx=3)

    def export_schedules(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="wemo-schedules.json")
        if not path:
            return
        try:
            jobs = [dict(schedule_fields(job), runner=source) for source, group in
                    (("Desktop app", self.schedule_store.read()), ("Background service", self.server_schedules))
                    for job in group]
            with open(path, "w", encoding="utf-8") as file:
                json.dump({"version": 2, "schedules": jobs}, file, indent=2)
        except (OSError, ValueError) as error:
            messagebox.showerror("Export failed", str(error))

    def import_schedules(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as file:
                backup = json.load(file)
            if not isinstance(backup, dict) or backup.get("version") not in (1, 2) or not isinstance(backup.get("schedules"), list):
                raise ValueError("Choose a Wemo Central Ops schedule backup.")
            entries = [schedule_fields(job) for job in backup["schedules"]]
            existing = [schedule_fields(job) for job in self.schedule_store.read() + self.server_schedules]
            unmatched = set()
            for job in entries:
                for target in job["targets"]:
                    device = find_device(target, {**self.known_devices_map, **self.live_devices})
                    if device:
                        target.update(device=device.name, device_id=device.udn)
                    else:
                        unmatched.add(target["device"])
                job.update(job["targets"][0])
                # Keep restores paused so the operator can check assignments
                # and runner ownership before any commands are sent.
                job["enabled"] = False
            runner = self.schedule_runner.get()
            if runner == "Background service" and self.schedule_server_status is None:
                raise ValueError("Connect to an updated background service first.")
            if runner not in ("Desktop app", "Background service"):
                raise ValueError("Choose a restore runner.")
            additions = []
            for job in entries:
                if any(same_schedule(job, other) for other in existing + additions):
                    continue
                additions.append(job)
            summary = f"Restore {len(additions)} schedules paused in {runner.lower()}?"
            if unmatched:
                summary += "\n\nMatch these devices using Edit: " + ", ".join(sorted(unmatched))
            if additions and messagebox.askyesno("Restore schedules", summary):
                if runner == "Background service":
                    if any(len(job["targets"]) > 1 for job in additions) and not self.schedule_server_status.get("multi_device_schedules"):
                        raise ValueError("Update the background service before restoring schedules for multiple devices.")
                    response = requests.post(f"{SERVER_URL}/api/schedules/restore", json=additions, timeout=5)
                    response.raise_for_status()
                    self.server_schedules = self.api.get_schedules()
                    self.persist_rule_inventory()
                else:
                    self.schedule_store.add(additions, skip_duplicates=True)
                    self.schedules = self.schedule_store.read()
                self.render_jobs()
            elif not additions:
                messagebox.showinfo("Restore schedules", "No new schedules to restore.")
        except (OSError, ValueError, TypeError, AttributeError, requests.RequestException) as error:
            messagebox.showerror("Restore failed", str(error))

    def _update_schedule_view(self, local, remote, status):
        self.schedules = local
        if remote is not None and remote != self.server_schedules:
            self.server_schedules = remote
            self.persist_rule_inventory()
        self.schedule_server_status = status
        self.sched_mode_lbl.configure(text="SERVICE ONLINE" if status else "DESKTOP APP ONLY")
        view_key = (self.schedules, self.server_schedules, status and status.get("solar"),
                    status is not None, datetime.datetime.now().replace(second=0, microsecond=0))
        if self.frames["sched"].winfo_ismapped() and view_key != self.schedule_view_key:
            self.schedule_view_key = view_key
            self.render_jobs()

    def _scheduler_engine(self):
        while self.monitoring:
            try:
                jobs = self.schedule_store.read()
                solar = self.solar.get_solar_times() if any(
                    job.get("enabled", True) and job["type"] in TRIGGERS[1:] for job in jobs) else None
                self.schedule_store.run_due({**self.known_devices_map, **self.live_devices}, solar)
                remote = status = None
                if self.api.connected:
                    try:
                        response = requests.get(f"{SERVER_URL}/api/schedules", timeout=2)
                        response.raise_for_status()
                        remote = response.json()
                        response = requests.get(f"{SERVER_URL}/api/schedule-status", timeout=2)
                        if response.ok:
                            status = response.json()
                    except (ValueError, requests.RequestException):
                        pass
                local = self.schedule_store.read()
                self.after(0, self._update_schedule_view, local, remote, status)
            except (ValueError, OSError) as error:
                self.after(0, self.sched_mode_lbl.configure, {"text": f"SCHEDULE ERROR: {error}"})
            time.sleep(5)

    def create_maintenance_ui(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["maint"] = f
        ctk.CTkLabel(f, text="Device Maintenance Tools", font=FONT_H1, text_color=COLOR_TEXT).pack(pady=20)
        sel = ctk.CTkFrame(f, fg_color=COLOR_FRAME); sel.pack(fill="x", padx=20, pady=10)
        self.maint_dev_combo = ctk.CTkComboBox(sel, values=["Scanning..."], width=300); self.maint_dev_combo.pack(side="left", padx=10)
        grid = ctk.CTkFrame(f, fg_color="transparent"); grid.pack(fill="both", expand=True, padx=20, pady=10)
        grid.columnconfigure(0, weight=1); grid.columnconfigure(1, weight=1); grid.columnconfigure(2, weight=1)
        
        c1 = ctk.CTkFrame(grid, fg_color=("#fff8e1", "#332222")); c1.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        ctk.CTkLabel(c1, text="Personal Data", font=FONT_H2, text_color=("#b38f00", "#ffcc00")).pack(pady=(15,5))
        ctk.CTkLabel(c1, text="Clears Name, Icon, Rules.\nKeeps Wi-Fi Connection.", text_color="gray", font=("Arial", 11)).pack(pady=5)
        ctk.CTkButton(c1, text="Reset Data (1)", fg_color=COLOR_MAINT_BTN_Y, text_color="#000000", command=lambda: self.run_reset_command(1)).pack(pady=15)
        
        c2 = ctk.CTkFrame(grid, fg_color=("#e3f2fd", "#222233")); c2.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        ctk.CTkLabel(c2, text="Wi-Fi Setup", font=FONT_H2, text_color=("#0055aa", "#66aaff")).pack(pady=(15,5))
        ctk.CTkLabel(c2, text="Clears Wi-Fi Credentials.\nReturns to Setup Mode.", text_color="gray", font=("Arial", 11)).pack(pady=5)
        ctk.CTkButton(c2, text="Reset Wi-Fi (5)", fg_color=COLOR_MAINT_BTN_B, text_color="#ffffff", command=lambda: self.run_reset_command(5)).pack(pady=15)
        
        c3 = ctk.CTkFrame(grid, fg_color=("#ffebee", "#330000")); c3.grid(row=0, column=2, sticky="nsew", padx=10, pady=10)
        ctk.CTkLabel(c3, text="Factory Default", font=FONT_H2, text_color=("#aa0000", "#ff4444")).pack(pady=(15,5))
        ctk.CTkLabel(c3, text="Wipes Everything.\nLike New Out-of-Box.", text_color="gray", font=("Arial", 11)).pack(pady=5)
        ctk.CTkButton(c3, text="Factory Reset (2)", fg_color=COLOR_MAINT_BTN_R, text_color="#ffffff", command=lambda: self.run_reset_command(2)).pack(pady=15)

    def update_maint_dropdown(self):
        names = sorted([d.name for d in self.known_devices_map.values()])
        if names: self.maint_dev_combo.configure(values=names); self.maint_dev_combo.set(names[0])
            
    def run_reset_command(self, reset_code):
        name = self.maint_dev_combo.get()
        dev = next((d for d in self.known_devices_map.values() if d.name == name), None)
        if not dev: return messagebox.showerror("Error", "Device not found.")
        if not messagebox.askyesno("Confirm Reset", f"Reset '{name}' (Code {reset_code})?"): return
        def task():
            try: dev.basicevent.ReSetup(Reset=reset_code); self.after(0, lambda: messagebox.showinfo("Success", "Command Sent"))
            except Exception as e: self.after(0, lambda: messagebox.showerror("Failure", str(e)))
        threading.Thread(target=task, daemon=True).start()

    def create_settings_ui(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent"); self.frames["settings"] = f
        ctk.CTkLabel(f, text="Application Settings", font=FONT_H1, text_color=COLOR_TEXT).pack(pady=(0, 20), anchor="w")
        c = ctk.CTkFrame(f, fg_color=COLOR_CARD); c.pack(fill="x", pady=10, padx=5)
        
        # [NEW] Added explicit labels for the settings
        r1 = ctk.CTkFrame(c, fg_color="transparent"); r1.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(r1, text="Appearance Mode:", font=FONT_BODY, text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkComboBox(r1, values=["System", "Light", "Dark"], command=self.change_theme, variable=ctk.StringVar(value=self.settings.get("theme", "System")), width=150).pack(side="right")
        
        r2 = ctk.CTkFrame(c, fg_color="transparent"); r2.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(r2, text="UI Scaling:", font=FONT_BODY, text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkComboBox(r2, values=["80%", "90%", "100%", "110%", "120%", "150%"], command=self.change_scaling, variable=ctk.StringVar(value=self.settings.get("scale", "100%")), width=150).pack(side="right")

    def change_theme(self, m): ctk.set_appearance_mode(m); self.settings["theme"]=m; self.save_json(SETTINGS_FILE, self.settings)
    def change_scaling(self, s): self.set_ui_scale(s); self.settings["scale"]=s; self.save_json(SETTINGS_FILE, self.settings)

    def show_qr_code(self):
        try:
            ip = NetworkUtils.get_local_ip(); url = f"http://{ip}:{SERVER_PORT}"
            qr = qrcode.QRCode(box_size=10, border=4); qr.add_data(url); qr.make(fit=True)
            img = ctk.CTkImage(light_image=qr.make_image(fill_color="black", back_color="white").convert("RGB"), dark_image=qr.make_image(fill_color="black", back_color="white").convert("RGB"), size=(250, 250))
            win = ctk.CTkToplevel(self); win.title("Mobile App"); win.geometry("400x480"); win.transient(self); win.focus_force(); self.set_icon(win)
            ctk.CTkLabel(win, image=img, text="").pack(padx=20, pady=(30, 20))
            ctk.CTkLabel(win, text="Scan to Control on Mobile", font=("Arial", 16, "bold")).pack(pady=(0,5))
            l=ctk.CTkLabel(win, text=url, text_color="gray", cursor="hand2", font=("Consolas", 14, "underline")); l.pack(pady=(0,20)); l.bind("<Button-1>", lambda e: webbrowser.open(url))
            ctk.CTkButton(win, text="Close", command=win.destroy, fg_color="gray").pack(pady=10)
        except Exception as e: messagebox.showerror("QR Error", str(e))

    def run_update_check(self):
        if not RELEASE_REPO:
            return
        h, n = UpdateManager.check_for_updates(VERSION, UPDATE_API_URL)
        if h: self.after(0, lambda: self.btn_update.configure(text=f"⬇ Get {n}") or self.btn_update.pack(side="bottom", padx=10, pady=(0, 10)))

    def start_local_server(self):
        cmd = [SERVICE_EXE_PATH] if SERVICE_EXE_PATH and os.path.exists(SERVICE_EXE_PATH) else [sys.executable, "wemo_server.py"]
        try:
            startupinfo = subprocess.STARTUPINFO() if sys.platform == "win32" else None
            if startupinfo: startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.Popen(cmd, startupinfo=startupinfo)
            self.svc_status.configure(text="Starting...", text_color="orange")
        except Exception as e: messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    app = WemoOpsApp()
    app.mainloop()
