# Wemo Central Ops handoff

This checkout is a macOS-focused working branch of `qrussell/wemo-ops-center`.
Use [ROADMAP.md](ROADMAP.md) for the dated project status, rationale, verification,
and open issues. Check `git status --short` and `git log -5 --oneline` at the
start of a session. The local handoff branch is `codex/wemo-central-ops`,
based on the original macOS fix commit `238a1f2`.

The fork is `fjimenez77/wemo-central-ops`, configured as `central-ops`. It is
public as of 2026-11-09 and carries the v5.3.2 release. Push to that remote
when authorized. The `origin` remote points to Quentin Russell's upstream
repository and should stay untouched.

Use `fjimenez77` as the author and committer name for new fork commits.
Use the configured email associated with that GitHub account. Do not add
Claude or AI attribution, generated-by lines, or AI co-author trailers to
commits, push notes, pull requests, or releases. Preserve existing upstream
credits and commit history.
[LICENSING.md](LICENSING.md) records the licence scope; the repository and the
v5.3.2 DMG were published on that basis, with the upstream root-licence gap
documented rather than resolved. The DMG is ignored by Git and is attached to
the GitHub release separately.

## Entry points

- `universal-app/wemo_ops_universal.py` is the desktop app and owns dashboard
  discovery, the Automation UI, the desktop scheduler, and device-rule reads.
- `universal-app/wemo_server.py` is the bundled Flask background service.
  Its schedule API supports editing, pausing, status, and batch restore.
- `universal-app/wemo_schedules.py` is shared by both runners. It validates
  schedules, stores them atomically, computes due times, and records results.
- `universal-app/wemo_rule_library.py` groups cached device rule copies and
  formats schedules for the graphical library. The desktop refreshes the
  inventory after discovery and periodically, and saves successful reports
  in `rule-library.json`. Rule ownership and reporting devices are separate.
- `universal-app/build_mac.sh` builds the service and app with PyInstaller,
  verifies the app signature, and packages the DMG. Use this directory for
  changes to the packaged Mac app. Other top-level platform folders contain
  earlier or separate implementations.

## Design decisions

- Mac discovery uses the active physical interface's IPv4 address and mask.
  It skips VPN tunnels and preserves a user-entered scan subnet.
- The desktop loads `desktop-devices.json` from its app data directory
  immediately. Cached cards are read-only until background verification
  confirms the device's UDN and current power state. A rescan merges by UDN
  and keeps missed devices offline with their last-seen timestamp.
- A schedule belongs to one runner. Desktop schedules run while the app is
  open, and service schedules run while the updated background service is
  active. The two stores are separate. Device-stored rules have a separate
  editor in `wemo_native_rules.py`. It supports fixed on/off intervals and
  auto-off countdowns that target only the selected device. Configure
  auto-off opens the existing countdown or starts a new one. The live
  disabled countdown create/edit/delete test preserved all five original
  rules on Night Outside Lights. Other rule
  types and shared rules can be viewed. New schedules use stable Wemo UDNs and older schedules
  still resolve by device name.
- Backups are JSON. Restore into the selected runner, match any missing
  device using Edit, then resume the paused rule. Native Wemo rules are
  outside the schedule backup format.

## Build and verify

From the repository root, use
`bash universal-app/build_mac.sh --python /opt/homebrew/bin/python3.14`.
Build in a scratch copy if keeping existing artifacts. Use
`codesign --verify --deep --strict` on the .app, `lipo -archs` on its main
executable, and `hdiutil verify` on the DMG. Local artifacts go under
`universal-app/dist/`. The current local artifact is
`WemoCentralOps_5.3.2_arm64.dmg`. It uses ad-hoc signing. The app bundle
uses a distinct ID and retains the old app data directory and service label.
Automation UI and scheduler smoke tests passed using simulated devices.

For hardware verification, test VPN discovery, a desktop schedule, a service
schedule with the desktop closed, and the rules read on a supported Wemo.
Asking the upstream maintainer to add a root LICENSE file remains open. It is
a follow-up, not a precondition: the repository and DMG are already public,
and LICENSING.md states the position taken.
