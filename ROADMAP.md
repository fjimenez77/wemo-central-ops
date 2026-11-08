# Wemo Ops Center — macOS Fix Roadmap

Working doc for our fork. Branch: `fix/mac-scan-and-signing`.
Upstream: [qrussell/wemo-ops-center](https://github.com/qrussell/wemo-ops-center) @ `58f4915`

> Local tracking file. Exclude from any upstream PR.

---

## Status

| Area | State |
|---|---|
| Scan regression (#21, #25) | Patched, benchmarked |
| macOS signing / "damaged app" (#21) | Patched, launch verified |
| UTF-16 source files | Fixed |
| App launch smoke test | Passed (30s, no `_tkinter` error) |
| README version drift | Fixed, macOS Gatekeeper section added |
| SSDP 10s-per-stale-host stall | Found, not fixed (see below) |
| Delivery decision (PR vs own fork) | Open |

---

## Done

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

Output: `universal-app/dist/WemoOps_<version>_<arch>.dmg`
Verified here: `WemoOps_5.3.1_arm64.dmg`, 79 MB, signature valid, launches.

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
- Launch the built `.app` and confirm the dashboard populates.
- Confirm the `Deep` checkbox toggles behaviour.
- Confirm Gatekeeper now says "unidentified developer" (recoverable) rather
  than "damaged" (dead end).

### 3. Decide delivery
- PR upstream, or maintain our own fork. Deferred until the fix is proven.
- Upstream has no `LICENSE` at root despite the MIT badge — worth resolving
  before contributing.

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
