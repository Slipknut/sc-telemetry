# 🛰️ sc-telemetry

A tiny, dependency-free tool that turns **MangoHud** capture logs into a
self-contained, interactive **Star Citizen performance dashboard** — built to
fill the gap left when CIG retired their public live telemetry.

Point it at a folder of MangoHud CSV captures and it produces a single
`dashboard.html` you can open locally or host anywhere. Stdlib Python only — no
`pip install`, nothing to set up.

![demo](docs/demo.png)

## Why

CIG used to publish live performance telemetry (FPS by location, etc.); that's
gone. But every player can capture their own with MangoHud — and the data tells
a clear story Star Citizen players know well: **the bottleneck flips by content.**

| Content | Typical result | Limited by |
|---|---|---|
| PU cities / contested zones (dense) | ~20–35 FPS, GPU coasting | **CPU / engine** (server-sim, main thread) |
| Open space / quantum | ~90+ FPS | balanced |
| Onyx facilities / Arena Commander | ~70 FPS, GPU ~90–95% | **GPU** |

The dashboard classifies each capture's bottleneck automatically, so you can see
exactly where (and why) your framerate goes — and whether a CPU or GPU upgrade
would even help your particular playstyle.

## Capture (MangoHud)

Enable MangoHud for the game and log a session (config: `~/.config/MangoHud/MangoHud.conf`):

```ini
output_folder=/home/you/sc-fps-logs
log_interval=100          # 10 Hz, ~3.4 MB/hour
toggle_logging=Shift_L+F2 # press to start a capture, press again to stop
```

Under **gamescope**, use `gamescope --mangoapp -- <game>` instead of `MANGOHUD=1`.
Each capture writes `mangoapp_<date>_<time>.csv` (+ a `_summary.csv`).

## Build the dashboard

```bash
python3 sc_telemetry.py                       # reads ~/sc-fps-logs → dist/dashboard.html
python3 sc_telemetry.py --logs DIR --out FILE --title "..."
```

Open the resulting HTML in any browser. It shows: a sortable session table,
per-session FPS + frametime traces, an FPS distribution histogram, headline
stats (avg / 1% / 0.1% lows, loads, temps, VRAM), and the bottleneck badge.

**Tag a capture with its zone:** drop a sibling file next to a capture, e.g.
`mangoapp_2026-06-01_23-29-13.label` containing `Onyx Facility`, and it shows up
as the session's zone.

## Try the demo

No captures yet? Generate synthetic demo sessions (clearly marked, kept in `samples/`):

```bash
python3 make_samples.py
python3 sc_telemetry.py --logs samples --out dist/demo.html --title "DEMO DATA"
```

## Privacy

MangoHud logs contain **only hardware/system info** (CPU/GPU/kernel) — **no Star
Citizen account, handle, or shard IDs.** So these captures are safe to share as-is.
(A future Game.log location-join would add PII and require scrubbing.)

## Roadmap

- [ ] Correlate captures with `Game.log` by timestamp → true **FPS-by-zone** maps
- [ ] Community aggregation (submit scrubbed captures → crowd FPS-by-zone/build)
- [ ] Trend-across-patches view (tag captures by game build)
- [ ] Offline build (vendor Chart.js instead of CDN)

## License

MIT
