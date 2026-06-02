# 🛰️ sc-telemetry

A tiny, dependency-free tool that turns **MangoHud** capture logs into a
self-contained, interactive **Star Citizen performance dashboard** — built to
fill the gap left when CIG retired their public live telemetry.

It auto-detects your Star Citizen install, reads each capture's **zone, build,
and graphics settings**, classifies the **bottleneck** (CPU/engine vs GPU), and
writes a single `dashboard.html` you can open locally or host anywhere.

![demo](docs/demo.png)

## What it shows

- **Session table** — when, zone, build, duration, avg / 1% / 0.1% FPS, GPU%/CPU%, bottleneck, temps, VRAM (sortable, click for detail)
- **By-zone comparison** — avg FPS per activity (Contested Zone vs Onyx vs mining vs space…)
- **Per-session charts** — FPS + frametime trace, FPS distribution histogram
- **Game settings** — resolution, window mode, VSync, HDR, DLSS/upscaling, and quality tiers (textures, shadows, volumetric clouds, …) read straight from your `attributes.xml`
- **Auto build/zone tags** — pulled from `Game.log` by timestamp; supports LIVE / PTU / EPTU / HOTFIX / EVOCATI (including renamed folders)
- **Linux runtime** *(Linux)* — Wine/Proton runner + version, gamescope (res/refresh/HDR), DXVK, esync/fsync, DLSS overrides, read from your LUG `sc-launch.sh` — the things that move FPS as much as in-game settings do
- **By-runtime comparison** *(Linux)* — opt in with `--install-hook` to stamp your runtime at each launch; swap runners between sessions and the dashboard charts avg FPS & 1% low *per runtime*, so you can see which Wine/Proton/gamescope/DXVK combo is actually fastest

## The story it tells

Star Citizen's bottleneck **flips by content**, and the dashboard makes it obvious:

| Content | Typical | Limited by |
|---|---|---|
| PU cities / contested zones | ~20–35 FPS, GPU coasting | **CPU / engine** (server-sim, main thread) |
| Open space / quantum | ~90+ FPS | balanced |
| Onyx facilities / Arena Commander | ~70 FPS, GPU ~90–95% | **GPU** |

So you can see whether a CPU or GPU upgrade would even help *your* playstyle.

## Install

**Option A — download a binary** (no Python needed): grab `sc-telemetry-linux-x86_64`
or `sc-telemetry-windows-x86_64.exe` from [Releases](../../releases), make it
executable, and run it.

**Option B — run from source** (Python 3.9+, stdlib only, nothing to install):
```bash
git clone https://github.com/Slipknut/sc-telemetry && cd sc-telemetry
python3 sc_telemetry.py
```

**Option C — build your own binary:** `./build.sh` (needs `pyinstaller`).

## First run

On first run it **auto-detects** your Star Citizen install. On Linux it reads
your **LUG** launch script (`sc-launch.sh`, found via its desktop shortcut or
prefix) to get the *exact* install path and runtime params; otherwise it probes
Wine/Proton prefixes, Steam libraries and common drives. Windows uses the
default `…\Roberts Space Industries\StarCitizen` locations. If nothing is found
it asks you to point at your `StarCitizen` folder (the one containing `LIVE` /
`PTU` / …) and remembers it. It scans all build channels and uses the
most-recently-played one for tagging + settings.

```bash
sc-telemetry                 # auto-detect, build dashboard, open it
sc-telemetry --sc /path/to/StarCitizen
sc-telemetry --logs ~/sc-fps-logs --out dashboard.html --open
sc-telemetry --no-sc         # MangoHud data only (skip Game.log/settings)
```

## Capture frames (MangoHud)

```bash
sc-telemetry --setup-mangohud
```
Writes a ready MangoHud config and prints how to launch SC with it. Then in-game:
**`Shift_L+F2`** start/stop a capture · **`Shift_R+F12`** toggle the overlay.
Under gamescope, launch with `gamescope --mangoapp -- <game>`.

**Tip:** capture per activity — start a log entering a Contested Zone, stop on
exit, start again in Onyx. Each becomes its own session, auto-tagged with zone +
build. (No captures yet? `python3 make_samples.py` makes demo data.)

**On Windows:** MangoHud is Linux-only, so capture with **[CapFrameX](https://www.capframex.com/)**
(free, open-source) — its bundled sensor service logs GPU/CPU load + temps, which
is what the bottleneck classification needs. Point `--logs` at your capture
folder. *(A CapFrameX/PresentMon importer is in progress — see roadmap.)* Game.log
zone/build tagging and `attributes.xml` settings work the same on Windows.

**Tagging:** captures are auto-tagged with zone/build/region from `Game.log`. To
label one yourself — or rename/override an auto-tag — use:
```bash
sc-telemetry --tag                              # interactively label every untagged capture
sc-telemetry --tag capture.csv --label "Onyx"   # set a label directly (overrides auto)
```
A manual label always wins over the auto-tag. (Captures with no in-game zone —
pure menu/launcher time — stay untagged unless you label them.)

**Compare runtimes (Linux):** run `sc-telemetry --install-hook` once — it adds a
runtime-stamp line to your LUG `sc-launch.sh` (idempotent, backs up first). Now
each launch records its Wine/Proton·gamescope·DXVK config; captures bind to the
runtime they ran under (by timestamp), and once you've played on ≥2 runtimes the
dashboard adds a **By runtime — avg FPS** chart. Don't use LUG? Add this to your
launch script yourself: `sc-telemetry --stamp-runtime`.

## Privacy

Captures contain only **hardware/system info** — **no account, handle, or shard
IDs**. The Game.log join reads your handle *only* to match location lines and
never writes it out. Dashboards are safe to share as-is.

## Roadmap

- [x] Self-contained dashboard from MangoHud logs
- [x] Game.log auto zone/build/region tagging + by-zone comparison
- [x] Graphics-settings panel from `attributes.xml`
- [x] Multi-build detection (LIVE/PTU/EPTU/HOTFIX/EVOCATI) + first-run config
- [x] LUG-aware auto-detect + Linux runtime panel (runner/gamescope/DXVK/sync)
- [x] Standalone Linux/Windows binaries (CI)
- [ ] Windows capture import (CapFrameX / PresentMon CSV + JSON → same session model)
- [ ] `level_stats` join → entity count + main-thread-vs-GPU "why"
- [ ] Screenshot thumbnails per capture
- [ ] Community aggregation (crowd FPS-by-zone/build) · trend-across-patches · offline Chart.js

## License

MIT
