#!/bin/bash
# ==============================================================================
#  WEMO CENTRAL OPS - macOS PACKAGER
#
#  Replaces the previous build_mac.sh / build_mac_app.sh / build_mac_tahoe.sh
#  trio. Differences that matter:
#    * preflights tkinter, so a Tk-less Python fails here instead of shipping
#      an .app that dies silently on launch  (issue #21)
#    * ad-hoc codesigns the nested service binary AND the bundle, in that
#      order, so Gatekeeper reports "unidentified developer" (recoverable)
#      instead of "is damaged and can't be opened" (a dead end)
#    * names the DMG after the architecture it actually contains
#
#  Usage: ./build_mac.sh [--arch native|universal2] [--sign "Developer ID ..."]
# ==============================================================================

set -euo pipefail

APP_NAME="Wemo Central Ops"
ARTIFACT_NAME="WemoCentralOps"
CLIENT_SCRIPT="wemo_ops_universal.py"
SERVER_SCRIPT="wemo_server.py"
BUNDLE_ID="org.wemocentralops.app"
# Keep the prior service label so upgrades replace the existing login agent.
SERVICE_LABEL="com.qrussell.wemoops"

ARCH_MODE="native"
SIGN_ID="-"          # "-" means ad-hoc. Pass --sign to use a real Developer ID.
PY=""                # interpreter to build with; auto-detected when empty

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH_MODE="${2:-native}"; shift 2 ;;
    --sign) SIGN_ID="${2:?--sign needs an identity}"; shift 2 ;;
    --python) PY="${2:?--python needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

VERSION=$(sed -n 's/^VERSION = "v\([0-9][0-9.]*\)"$/\1/p' "$CLIENT_SCRIPT")
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid application version" >&2; exit 1; }

HOST_ARCH="$(uname -m)"
case "$ARCH_MODE" in
  native)     TARGET_ARCH="$HOST_ARCH" ;;
  universal2) TARGET_ARCH="universal2" ;;
  *) echo "ERROR: --arch must be 'native' or 'universal2'" >&2; exit 2 ;;
esac

echo "========================================="
echo "   WEMO CENTRAL OPS - macOS PACKAGER (.DMG)"
echo "   Version:  $VERSION"
echo "   Host:     macOS $(sw_vers -productVersion) / $HOST_ARCH"
echo "   Building: $TARGET_ARCH"
echo "========================================="

# ---------------------------------------------------------------- 1. PREFLIGHT
echo "[1/5] Preflight..."

for tool in hdiutil codesign lipo; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool not found." >&2; exit 1; }
done

# Pick an interpreter that is both modern enough and has Tk. Do NOT just take
# `python3`: on macOS that is usually Apple's 3.9.6, and `brew install
# python@3.x` does not create a `python3` symlink at all. A Tk-less Python is
# the top cause of a Wemo Central Ops.app that dies on launch with
# "ModuleNotFoundError: No module named '_tkinter'"  (issue #21).
tk_ok() { "$1" -c 'import sys,tkinter; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)' >/dev/null 2>&1; }

if [[ -n "$PY" ]]; then
  tk_ok "$PY" || { echo "ERROR: $PY is missing tkinter or older than 3.11." >&2; exit 1; }
else
  for cand in \
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
      python3.14 python3.13 python3.12 python3.11 python3; do
    cand_path="$(command -v "$cand" 2>/dev/null || true)"
    [[ -n "$cand_path" ]] || continue
    if tk_ok "$cand_path"; then PY="$cand_path"; break; fi
  done
fi

if [[ -z "$PY" ]]; then
  cat >&2 <<'MSG'

ERROR: no Python >= 3.11 with working tkinter was found, so any app built
here would not launch.

Install one, then re-run:

  Homebrew:    brew install python@3.14 python-tk@3.14
               (note: this installs `python3.14`, not `python3`)
  python.org:  https://www.python.org/downloads/macos/
               Tk is bundled, and it is the only option that can produce a
               real --arch universal2 build.

Then verify:   python3.14 -c "import tkinter; print(tkinter.TkVersion)"
Or point this script at it:   ./build_mac.sh --python /path/to/python3

MSG
  exit 1
fi

PY_VER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "   > python   $PY_VER  ($PY)"
echo "   > tkinter  Tk $("$PY" -c 'import tkinter;print(tkinter.TkVersion)')"

# A universal2 app needs a universal2 interpreter. Homebrew's is single-arch.
if [[ "$TARGET_ARCH" == "universal2" ]]; then
  PY_BIN="$("$PY" -c 'import sys;print(sys.executable)')"
  if ! lipo -archs "$PY_BIN" 2>/dev/null | grep -q 'x86_64.*arm64\|arm64.*x86_64'; then
    cat >&2 <<MSG

ERROR: --arch universal2 needs a universal2 Python. This one is single-arch:

  $PY_BIN -> $(lipo -archs "$PY_BIN" 2>/dev/null || echo unknown)

Install Python from python.org (its macOS installer is universal2) or build
with --arch native and ship one DMG per architecture.

MSG
    exit 1
  fi
  echo "   > universal2 Python OK"
fi

# ------------------------------------------------------------------- 2. VENV
echo "[2/5] Setting up build environment..."
rm -rf .venv_mac
"$PY" -m venv .venv_mac
# shellcheck disable=SC1091
source .venv_mac/bin/activate
trap 'deactivate 2>/dev/null || true' EXIT

pip install --upgrade pip --quiet
pip install --quiet \
  "pywemo>=2.1.1" "customtkinter>=6.0.0" requests pyinstaller pyperclip Pillow \
  flask qrcode waitress

# tkinter lives in the interpreter, not in a wheel -- re-check inside the venv.
python -c 'import tkinter' 2>/dev/null || {
  echo "ERROR: the venv lost tkinter. Is python3-tk installed for ${PY_VER}?" >&2
  exit 1
}

# ---------------------------------------------------------------- 3. COMPILE
echo "[3/5] Compiling..."
rm -rf build dist

PYI_ARCH=()
if [[ "$TARGET_ARCH" == "universal2" ]]; then PYI_ARCH=(--target-arch universal2); fi

ICON_FLAG=() ; ADD_DATA=()
ICON_FLAG=(--icon images/WemoCentralOps.icns)
ADD_DATA=(--add-data "images/WemoCentralOps.png:images")

echo "   > service binary..."
pyinstaller --noconfirm --noconsole --onefile --clean \
  --name wemo_service \
  ${PYI_ARCH[@]+"${PYI_ARCH[@]}"} \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --hidden-import pywemo \
  --hidden-import flask \
  --hidden-import waitress \
  "$SERVER_SCRIPT" >/dev/null

# The nested binary must carry its own valid signature BEFORE it is sealed
# into the .app, or the bundle's signature will not validate and macOS
# reports the app as damaged.
echo "   > signing service binary..."
codesign --force --timestamp=none --sign "$SIGN_ID" dist/wemo_service

echo "   > GUI app..."
pyinstaller --noconfirm --windowed --clean \
  --name "$APP_NAME" \
  ${PYI_ARCH[@]+"${PYI_ARCH[@]}"} \
  ${ICON_FLAG[@]+"${ICON_FLAG[@]}"} \
  ${ADD_DATA[@]+"${ADD_DATA[@]}"} \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --add-binary "dist/wemo_service:." \
  --collect-all customtkinter \
  --collect-all PIL \
  --hidden-import pywemo \
  --hidden-import pyperclip \
  --hidden-import qrcode \
  --hidden-import PIL._tkinter_finder \
  --hidden-import PIL.ImageTk \
  "$CLIENT_SCRIPT" >/dev/null

python - "dist/$APP_NAME.app/Contents/Info.plist" "$VERSION" <<'PYTHON'
import plistlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
metadata = plistlib.loads(path.read_bytes())
metadata["CFBundleShortVersionString"] = sys.argv[2]
metadata["CFBundleVersion"] = sys.argv[2]
path.write_bytes(plistlib.dumps(metadata))
PYTHON

deactivate || true
trap - EXIT

[[ -d "dist/$APP_NAME.app" ]] || { echo "ERROR: no app bundle produced." >&2; exit 1; }

# ------------------------------------------------------------------ 4. SIGN
echo "[4/5] Signing bundle..."
# Re-seal the whole bundle, including the dylibs PyInstaller vendored in.
#
# Hardened Runtime (--options runtime) is required for notarization but it
# BREAKS an ad-hoc signed bundle: an ad-hoc signature carries no Team ID, so
# macOS library validation refuses to load the bundled Python framework and
# the app dies before it draws a window --
#   "code signature in .../Python.framework/Versions/X/Python not valid for
#    use in process: mapping process and mapped file (non-platform) have
#    different Team IDs"
# So enable it only for a real Developer ID, paired with the entitlement that
# lets a Python app load libraries Apple did not sign.
SIGN_ARGS=(--force --deep --timestamp=none --sign "$SIGN_ID")

if [[ "$SIGN_ID" != "-" ]]; then
  ENTITLEMENTS="$(mktemp -t wemoops-entitlements).plist"
  cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
PLIST
  SIGN_ARGS+=(--options runtime --entitlements "$ENTITLEMENTS")
  echo "   > hardened runtime + library-validation entitlement"
fi

codesign "${SIGN_ARGS[@]}" "dist/$APP_NAME.app"

# Use codesign's exit status, not its text. Piping it into `grep -q` under
# `set -o pipefail` makes grep close the pipe early, codesign dies of SIGPIPE
# and a valid signature reads as a failure.
if codesign --verify --deep --strict "dist/$APP_NAME.app" 2>/dev/null; then
  echo "   > signature valid"
else
  codesign --verify --deep --strict --verbose=2 "dist/$APP_NAME.app" || true
  echo "ERROR: bundle signature did not validate; the app would launch as 'damaged'." >&2
  exit 1
fi

if [[ "$SIGN_ID" == "-" ]]; then
  echo "   > NOTE: ad-hoc signed and NOT notarized."
  echo "     First launch shows 'unidentified developer'; right-click > Open,"
  echo "     or run Install_WemoCentralOps.command to clear the quarantine flag."
fi

# ------------------------------------------------------------------- 5. DMG
echo "[5/5] Packaging DMG..."
STAGING="dist/dmg_staging"
rm -rf "$STAGING"
mkdir -p "$STAGING/Service_Binary"

cp -R "dist/$APP_NAME.app" "$STAGING/"
cp dist/wemo_service "$STAGING/Service_Binary/"
ln -s /Applications "$STAGING/Applications"
cp ../LICENSE ../NOTICE ../LICENSING.md "$STAGING/"

cat > "$STAGING/READ_ME_FIRST.txt" <<TXT
Wemo Central Ops $VERSION ($TARGET_ARCH)

INSTALL
  Drag $APP_NAME.app onto the Applications shortcut in this window.

IF macOS SAYS THE APP CANNOT BE OPENED
  This build is signed, but not notarized by Apple (notarization requires a
  paid Developer ID). Either:
    - right-click $APP_NAME.app in Applications and choose Open, then Open
      again at the prompt; or
    - open System Settings > Privacy & Security and click "Open Anyway"; or
    - run Install_WemoCentralOps.command in this window, which copies the app and
      clears the download quarantine flag for you.

OPTIONAL: 24/7 BACKGROUND SERVICE
  Install_WemoCentralOps.command also registers wemo_service as a login agent on
  port 5050. With it running the desktop app loads devices from the service
  instead of sweeping the network, which is much faster.
TXT

cat > "$STAGING/Install_WemoCentralOps.command" <<EOF
#!/bin/bash
set -e
DIR="\$( cd "\$( dirname "\${BASH_SOURCE[0]}" )" && pwd )"
APP_NAME="$APP_NAME"

echo "Installing \$APP_NAME..."
echo "Your password is needed to copy into /Applications."
sudo -v

echo "[1/3] Copying to /Applications..."
sudo rm -rf "/Applications/\$APP_NAME.app"
sudo cp -R "\$DIR/\$APP_NAME.app" /Applications/
# Ownership must end up with the user, not root, or updates fail later.
sudo chown -R "\$(id -u):\$(id -g)" "/Applications/\$APP_NAME.app"
xattr -dr com.apple.quarantine "/Applications/\$APP_NAME.app" 2>/dev/null || true

echo "[2/3] Installing background service..."
DATA_DIR="\$HOME/Library/Application Support/WemoOps"
mkdir -p "\$DATA_DIR"
cp "\$DIR/Service_Binary/wemo_service" "\$DATA_DIR/"
chmod +x "\$DATA_DIR/wemo_service"
xattr -dr com.apple.quarantine "\$DATA_DIR/wemo_service" 2>/dev/null || true

echo "[3/3] Registering login agent..."
PLIST="\$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist"
mkdir -p "\$HOME/Library/LaunchAgents"
cat > "\$PLIST" <<PLIST_CONTENT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$SERVICE_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>\$DATA_DIR/wemo_service</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>\$HOME/Library/Logs/\$APP_NAME.log</string>
    <key>StandardErrorPath</key>
    <string>\$HOME/Library/Logs/\$APP_NAME.err.log</string>
</dict>
</plist>
PLIST_CONTENT

launchctl unload "\$PLIST" 2>/dev/null || true
launchctl load "\$PLIST"

echo ""
echo "Done. Service on port 5050; launch \$APP_NAME from Applications."
EOF
chmod +x "$STAGING/Install_WemoCentralOps.command"

DMG="dist/${ARTIFACT_NAME}_${VERSION}_${TARGET_ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGING" \
               -ov -format UDZO "$DMG" >/dev/null
codesign --force --sign "$SIGN_ID" "$DMG" 2>/dev/null || true

echo ""
echo "========================================="
echo "   SUCCESS"
echo "   $DMG"
echo "   $(du -h "$DMG" | cut -f1) / $TARGET_ARCH"
echo "========================================="
