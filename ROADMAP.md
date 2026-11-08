# Wemo Central Ops — macOS Fix Roadmap

Working doc for our fork. Branch `codex/wemo-central-ops`. Last updated November 8, 2026.
Upstream: [qrussell/wemo-ops-center](https://github.com/qrussell/wemo-ops-center) @ `58f4915`

> Local tracking file. Exclude from any upstream PR.

---

## Current checkout

Commit `624306a` contains the scan performance and macOS packaging changes.
The local fork branch includes VPN-aware discovery, the desktop device
cache, the Rules Library, and Wemo Central Ops branding.
See [CLAUDE.md](CLAUDE.md) for the project entry points and handoff.

## Status

| Area | State |
|---|---|
| Scan regression (#21, #25) | Patched, benchmarked |
| macOS signing / "damaged app" (#21) | Patched, launch verified |
| UTF-16 source files | Fixed |
| Initial arm64 branch build | Valid signature and DMG checksum. Process started; dashboard inspection timed out. |
| Wi-Fi and Ethernet subnet detection | Verified on this Mac with an active IPsec interface and with simulated /27 and /20 masks. |
| Desktop device cache | Tested with simulated devices, including identity verification, rename and IP changes, and offline retention. |
| Automation | Implemented in the working tree. Live hardware verification remains open. |
| Automation smoke tests | Passed on macOS with simulated devices. Covered execution, pause, duplicate prevention, missing-device results, service API, and desktop UI flows. |
| Latest arm64 package | `universal-app/dist/WemoCentralOps_5.3.1_arm64.dmg` (79 MB). Bundle and service signatures, executable architecture, and DMG checksum verified. |
| README version drift | Fixed, macOS Gatekeeper section added |
| SSDP 10s-per-stale-host stall | Found, not fixed (see below) |
| Delivery decision (PR vs own fork) | Open |

---

## Why the latest changes were made

- **Automation keyboard controls.** F6 opens the managed Rules Library,
  F7 opens the device picker, F8 saves the schedule, and F9 opens schedule
  management. The picker supports arrows, Space, and Return. Schedule
  management supports arrows, Return to edit, and Delete with confirmation.
- **Multi-device Wemo Ops schedules.** Choose multiple Wemos in the managed
  schedule editor. The library displays all targets under Controls on one
  card. Each runner attempts every target and reports partial failures.
  Existing single-device schedules and version 1 backups still load.
  Multi-device schedules on the background runner require the updated service.
- **Graphical Rules Library.** Automation opens with an inventory of rules
  from the Wemos and Wemo Ops. The desktop reads reachable devices after
  discovery and refreshes them every five minutes. The last successful
  results persist in `rule-library.json`, including rules from offline
  devices. Matching copies share one card. The card separates controlled
  devices from the devices that reported the rule. Copies with different
  contents are shown separately. Supported edits use the existing source
  editors and device writes still check for a changed database.
- **Desktop device cache.** The dashboard loads last-known devices from
  `desktop-devices.json` at startup. Cached cards show their last state and
  last-seen time. Background verification checks the device's UDN and
  reads live power state before offering controls. Scan merges discoveries
  by UDN and keeps missing devices visible as offline. The dashboard sorts
  devices on the detected subnet first. The cache is separate from the
  service's device registry and contains no Wi-Fi credentials.
- **Subnet detection.** The Mac app used the route to `8.8.8.8` and assumed
  a /24. It now reads the IPv4 addresses and masks of active `enN`
  interfaces, skips tunnel interfaces, and uses macOS's primary interface
  when it is among the physical candidates. If several remain without a
  usable primary, it chooses the lowest-numbered interface. Scan refreshes
  the automatic value and preserves manual input. On macOS an explicit
  subnet scan runs locally even when the service is up. SSDP still runs when
  the subnet is larger than /16, while the full TCP sweep is skipped.
- **Rules and execution.** The previous Automation list showed a target,
  days, trigger type, and action. The local scheduler reported a mode without
  executing jobs. The desktop now runs its own schedule store while open,
  and the background service runs its separate store. Automation shows the
  runner, exact trigger, next run, and last recorded result. Solar times for
  later days are estimates based on today's data.
- **Device assignments.** The dashboard Rules button opens Automation
  filtered to the selected device. Edit and pause controls work for desktop
  and updated service schedules. New assignments store the Wemo UDN so a
  rename does not break the target. Existing name-based schedules keep
  working. The Stored on device tab reads the selected Wemo's rules database
  through pywemo and shows the entries and target IDs or known names. Some
  firmware fields are shown as raw codes.
- **Native rule editing.** Create, edit, and delete fixed on/off intervals
  and auto-off countdowns that target the selected device alone. The app
  backs up the original archive in its `native-rule-backups` directory,
  checks the firmware version and archive before uploading, and verifies
  the updated database by reading it back. Shared and other rule types
  remain viewable. Live reads succeeded on five Wemos. On Night Outside
  Lights, a disabled 5-minute countdown was created, edited to 7 minutes,
  and deleted. The five original rules and their database records matched
  afterward. The device database version advanced from 4 to 7. Enabled
  countdown execution still needs hardware verification.
- **Rule ownership and auto-off.** The Wemo Ops tab contains the managed
  scheduler and runner selection. The device tab has its own creation and
  editing controls. Configure auto-off opens the device's existing
  supported countdown or starts a new one with the delay, days, and
  enabled state available to configure.
- **Backup and restore.** JSON export includes the desktop and loaded
  service schedules. Restore adds schedules paused to the selected runner
  and skips duplicates. Assign unmatched devices with Edit before resuming.
- **Persistence.** `universal-app/wemo_schedules.py` handles validation,
  atomic writes, job claims, and results. The Mac desktop store remains
  under `~/Library/Application Support/WemoOps`. The service continues to
  use its own existing data directory. On macOS it also reads the desktop's
  saved solar coordinates.

## Earlier changes

- **Scan regression.** `_scan_task` lost its deep-scan gate and the server
  fast path in 5.3.0, so every refresh ran a full TCP sweep. Restored the
  gate (`Deep` checkbox, on by default), restored server delegation, restored
  the progress callback.
- **Scan performance.** Probe phase now sends each `host:port` pair as its own
  task, so a dead host costs one timeout instead of four. Verify phase is
  parallel with a hard 1.5s cap and a cheap Belkin check before pywemo.
  Measured on a /24: **12.0s → 2.9s (4.2x)**.
- **Same fix applied to `wemo_server.py`**, which carried its own copy of the
  buggy `DeepScanner`.
- **macOS build.** Three divergent scripts (`build_mac.sh` 5.3.0-1,
  `build_mac_app.sh` 5.1.6, `build_mac_tahoe.sh` 5.3.0) consolidated into one
  `build_mac.sh`. Adds: tkinter preflight, interpreter auto-detect, ad-hoc
  codesign of the nested service binary *then* the bundle, signature
  verification, honest arch-named DMG, `--arch universal2` opt-in.
- **Two signing traps found by actually launching the build:**
  - `--options runtime` (Hardened Runtime) *breaks* an ad-hoc signed app. An
    ad-hoc signature has no Team ID, so library validation refuses the bundled
    Python framework and the app dies before drawing a window. Now applied
    only with a real `--sign` identity, paired with the
    `com.apple.security.cs.disable-library-validation` entitlement.
  - `codesign ... | grep -q` under `set -o pipefail` reports a *valid*
    signature as a failure: grep closes the pipe, codesign dies of SIGPIPE.
    Check codesign's exit status instead.
- **Build environment note.** `brew install python-tk@3.14` upgraded
  `python@3.14` 3.14.6 → 3.14.10 and removed the `/opt/homebrew/bin/python3`
  symlink, so bare `python3` is now Apple's 3.9.6. The build script no longer
  trusts `python3`; it probes for a Tk-capable interpreter >= 3.11. Any other
  venv on this machine built against 3.14.6 may need recreating.
- **UTF-16LE → UTF-8** for `.gitignore` (every ignore rule was silently dead)
  and `requirements.txt` (unparseable by pip).
- **`pywemo` pin** aligned with what the build scripts actually install.

---

## How to build

```bash
cd universal-app
./build_mac.sh                      # native arch, ad-hoc signed
./build_mac.sh --arch universal2    # needs a python.org universal2 Python
./build_mac.sh --sign "Developer ID Application: Name (TEAMID)"
./build_mac.sh --python /path/to/python3
```

Output: `universal-app/dist/WemoCentralOps_<version>_<arch>.dmg`
Use `--python /opt/homebrew/bin/python3.14` on this Mac. The packager
recreates its build, dist, and build environment directories, so use a
scratch copy to preserve earlier DMGs. The default build is ad-hoc signed.
Apple notarization is a separate step.

Upstream shipped two confusingly named DMGs per release
(`_tahoe` and `_universal`) and **neither was universal2** — no script passed
`--target-arch universal2`, and a true fat binary would be ~1.8x the size of a
thin one, not 1.06x. The DMG is now named after the architecture it actually
contains.

---

## To do

### 1. Review the MCP server
- Repo: https://github.com/apiarya/wemo-mcp-server
- Split out of this repo at v1.1.0; `pip install wemo-mcp-server`.
- Check: does it carry the same `DeepScanner` discovery bug? It was extracted
  from `mcp/` in this tree before our fix, so it probably does.
- Also review: tool surface, auth/network assumptions, test coverage
  (`mcp/tests/` had `test_e2e.py` + `test_server.py` upstream).

### 2. Finish macOS verification
- Launch the packaged app from the latest DMG and confirm discovery,
  execution, and device-rule reading against live Wemo hardware.
- Test a service-owned rule with the desktop closed. Confirm the installed
  service is the updated binary, then check the last result in Automation.
- Test native rule layouts across multiple Wemo models and firmware versions.
- Confirm the `Deep` checkbox toggles behaviour.
- Confirm Gatekeeper now says "unidentified developer" (recoverable) rather
  than "damaged" (dead end).

### 3. Prepare publication
- Publish Wemo Central Ops in a separate repository when the release
  requirements in [LICENSING.md](LICENSING.md) are satisfied.
- Ask the upstream maintainer to confirm MIT scope for the desktop app,
  service, and assets, and provide the copyright notice before publishing a
  modified DMG. The root README has an MIT badge and the only license file
  found was in `mcp/`.

### 4. SSDP phase still pays pywemo's 10s timeout
Found while smoke-testing the built app. `pywemo.discover_devices()` is the
*first* phase of every scan, and pywemo's `REQUESTS_TIMEOUT` is **10 seconds**:

```
Failed to fetch description http://10.22.22.1:49153/setup.xml
ConnectTimeoutError: Connection to 10.22.22.1 timed out. (connect timeout=10)
```

Every stale SSDP announcement costs a full 10s, serially, before our (now
fast) deep scan even starts. This is inside pywemo, not our code, so it needs
either a bounded wrapper around `discover_devices()` or a pre-filter that
drops unreachable hosts first. Not fixed.

### 5. Candidates, not yet scoped
- **`#25` cross-check.** Our scan fix should close the Windows 300s report
  too; same root cause, same code path.
- **Six copies of the app.** `windows/`, `mac/`, `linux/`, `universal/`,
  `universal-app/`, `universal-server/` are near-duplicates. Only
  `universal-app/` is live. The dead trees are a trap for future fixes.
- **No tests anywhere** in the desktop/server code. CI only publishes Docker
  and PyPI artifacts; nothing runs.
- **README drift.** Badges say v5.2.3, latest release is v5.3.0.
- **No notarization.** Ad-hoc signing is as far as we can go without a paid
  Apple Developer ID. Document the right-click-Open path in the README.

---

## Open issues upstream worth tracking

| # | Title | Relevance |
|---|---|---|
| 21 | 5.3.1 not running on MAC | What we are fixing |
| 25 | 5.3.0 takes 300+s to discover on Windows | Same root cause |
| 24 | App not detecting all Wemo devices | Deep-scan gate may help |
| 30 | Device missing from scan after provisioning | Discovery path |
| 22 | Allow remote server URL | Touches the fast path we restored |
