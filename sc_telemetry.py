#!/usr/bin/env python3
"""
sc-telemetry — turn MangoHud Star Citizen capture logs into a self-contained
interactive dashboard.html. Stdlib only (no pip installs).

Usage:
    python3 sc_telemetry.py [--logs DIR] [--out FILE] [--title STR]

Defaults: --logs ~/sc-fps-logs   --out dist/dashboard.html

Optional per-capture content tag: drop a sibling text file next to a capture,
e.g. `mangoapp_2026-06-01_23-29-13.label` containing "Onyx Facility", and it'll
be used as the session's label/zone in the dashboard.
"""
import csv, json, os, glob, argparse, statistics, datetime, re, sys, subprocess
from pathlib import Path
try:
    import scpaths, gamelog, mango, linuxenv
    import settings as scset
except ImportError:               # running a module standalone / not all present
    scpaths = gamelog = mango = scset = linuxenv = None

# ── MangoHud parsing ─────────────────────────────────────────────────────────
# Capture CSV layout: line0 = meta keys, line1 = meta values, line2 = column
# header, line3+ = samples. `elapsed` (last col) is nanoseconds since log start.

def _f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d

def parse_capture(path):
    with open(path, newline="", errors="ignore") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 4:
        return None
    meta = dict(zip(rows[0], rows[1]))
    cols = rows[2]
    idx = {c: i for i, c in enumerate(cols)}
    def col(row, name):
        i = idx.get(name)
        return _f(row[i]) if i is not None and i < len(row) else 0.0
    samples = [r for r in rows[3:] if r and r[0].replace(".", "", 1).isdigit()]
    return {"meta": meta, "idx": idx, "samples": samples, "col": col}

def parse_summary(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", errors="ignore") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return {}
    return dict(zip(rows[0], rows[1]))

def downsample(xs, n=700):
    if len(xs) <= n:
        return [round(v, 2) for v in xs]
    step = len(xs) / n
    out = []
    for i in range(n):
        a, b = int(i * step), int((i + 1) * step)
        chunk = xs[a:b] or [xs[min(a, len(xs) - 1)]]
        out.append(round(sum(chunk) / len(chunk), 2))
    return out

def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p)))
    return sorted_vals[k]

def classify_bottleneck(gpu_avg, fps_avg):
    # SC main-thread/engine limits show as GPU coasting (headroom) at low fps;
    # a maxed GPU means GPU-bound. Heuristic on average GPU load:
    if gpu_avg >= 85:
        return "GPU-bound"
    if gpu_avg < 75:
        return "CPU / engine-bound"
    return "Balanced"

def label_for(path):
    base = re.sub(r"\.csv$", "", os.path.basename(path))
    for ext in (".label", ".txt"):
        lp = os.path.join(os.path.dirname(path), base + ext)
        if os.path.exists(lp):
            with open(lp, errors="ignore") as fh:
                t = fh.readline().strip()
                if t:
                    return t
    return None

def parse_dt(path):
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", os.path.basename(path))
    if m:
        try:
            return datetime.datetime.strptime(m.group(1) + " " + m.group(2),
                                               "%Y-%m-%d %H-%M-%S")
        except ValueError:
            pass
    return datetime.datetime.fromtimestamp(os.path.getmtime(path))

def analyze(path, logidx=None, rtidx=None):
    cap = parse_capture(path)
    if not cap:
        return None
    col, samples = cap["col"], cap["samples"]
    fps = [col(r, "fps") for r in samples]
    ft = [col(r, "frametime") for r in samples]
    cpu = [col(r, "cpu_load") for r in samples]
    gpu = [col(r, "gpu_load") for r in samples]
    ctmp = [col(r, "cpu_temp") for r in samples]
    gtmp = [col(r, "gpu_temp") for r in samples]
    vram = [col(r, "gpu_vram_used") for r in samples]
    ram = [col(r, "ram_used") for r in samples]
    elapsed = [col(r, "elapsed") for r in samples]
    if not fps:
        return None
    dur = (elapsed[-1] - elapsed[0]) / 1e9 if len(elapsed) > 1 else len(fps) * 0.1
    sfps = sorted(fps)
    summ = parse_summary(re.sub(r"\.csv$", "_summary.csv", path))
    gpu_avg = statistics.mean(gpu) if gpu else 0.0
    fps_avg = statistics.mean(fps)
    # relative time axis in seconds
    t0 = elapsed[0] if elapsed else 0
    tsec = [round((e - t0) / 1e9, 1) for e in elapsed]
    # FPS histogram (10-fps bins)
    hi = int((max(fps) // 10 + 1) * 10) if fps else 10
    edges = list(range(0, max(hi, 20) + 10, 10))
    counts = [0] * (len(edges) - 1)
    for v in fps:
        b = min(int(v // 10), len(counts) - 1)
        counts[b] += 1
    meta = cap["meta"]
    manual = label_for(path)
    enr = {}
    if logidx and gamelog:
        try:
            enr = gamelog.enrich(parse_dt(path), dur, logidx)
        except Exception:
            enr = {}
    rt_key = ""
    if rtidx and linuxenv:
        try:
            rt = linuxenv.match(parse_dt(path), rtidx)
            rt_key = rt.get("label", "") if rt else ""
        except Exception:
            rt_key = ""
    return {
        "file": os.path.basename(path),
        "label": manual or enr.get("zone") or "",
        "events": enr.get("events", []),
        "runtime_key": rt_key,
        "build": enr.get("build", ""),
        "region": enr.get("region") or "",
        "session": enr.get("session"),
        "datetime": parse_dt(path).strftime("%Y-%m-%d %H:%M"),
        "start_iso": parse_dt(path).strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(dur, 1),
        "samples": len(fps),
        "fps": {
            "avg": round(fps_avg, 1),
            "median": round(statistics.median(fps), 1),
            "min": round(min(fps), 1),
            "max": round(max(fps), 1),
            "p1": round(_f(summ.get("1% Min FPS")) or pct(sfps, 0.01), 1),
            "p01": round(_f(summ.get("0.1% Min FPS")) or pct(sfps, 0.001), 1),
        },
        "frametime": {
            "avg": round(statistics.mean(ft), 1) if ft else 0,
            "p99": round(pct(sorted(ft), 0.99), 1) if ft else 0,
            "max": round(max(ft), 1) if ft else 0,
        },
        "load": {"cpu_avg": round(statistics.mean(cpu), 1) if cpu else 0,
                 "gpu_avg": round(gpu_avg, 1)},
        "temps": {"cpu_avg": round(statistics.mean(ctmp), 1) if ctmp else 0,
                  "gpu_avg": round(statistics.mean(gtmp), 1) if gtmp else 0,
                  "cpu_peak": round(max(ctmp), 1) if ctmp else 0,
                  "gpu_peak": round(max(gtmp), 1) if gtmp else 0},
        "vram": {"avg": round(statistics.mean(vram), 1) if vram else 0,
                 "peak": round(max(vram), 1) if vram else 0},
        "ram_avg": round(statistics.mean(ram), 1) if ram else 0,
        "bottleneck": classify_bottleneck(gpu_avg, fps_avg),
        "hw": {"cpu": meta.get("cpu", "").strip() or "?",
               "gpu": (meta.get("gpu", "").strip() or "?"),
               "kernel": meta.get("kernel", "").strip(),
               "scheduler": meta.get("cpuscheduler", "").strip()},
        "series": {"t": tsec if len(tsec) <= 700 else downsample(tsec),
                   "fps": downsample(fps), "frametime": downsample(ft)},
        "hist": {"edges": edges, "counts": counts},
    }

def build_data(logs_dir, channels=None, sc_dir=None):
    logidx, settings_data, active, chan_list, runtime = None, {}, "", [], None
    if channels:
        prim = scpaths.primary_channel(channels) if scpaths else None
        if prim:
            active = prim["channel"]
            if gamelog:
                try: logidx = gamelog.index_sessions(prim["dir"])
                except Exception: logidx = None
            if prim.get("attr") and scset:
                try: settings_data = scset.parse_settings(prim["attr"])
                except Exception: settings_data = {}
        chan_list = [{"channel": c["channel"], "last_used": c["last_used"],
                      "has_logs": c["has_logs"]} for c in channels]
    if linuxenv and os.name != "nt" and sc_dir:   # only when an install resolved
        try: runtime = linuxenv.parse(sc_dir)
        except Exception: runtime = None
    rtidx = linuxenv.load_snapshots(logs_dir) if linuxenv else []
    runtimes = {}                                  # label → representative chips
    for _when, rt in rtidx:
        runtimes[rt.get("label", "?")] = {"chips": rt.get("chips", []),
                                          "stamped_at": rt.get("stamped_at", "")}
    files = sorted(f for f in glob.glob(os.path.join(logs_dir, "*.csv"))
                   if "_summary" not in f)
    sessions = [s for s in (analyze(f, logidx, rtidx) for f in files) if s]
    sessions.sort(key=lambda s: s["datetime"])
    total_s = sum(s["duration_s"] for s in sessions)
    if sessions and any(s["hw"].get("gpu", "?") in ("?", "") for s in sessions):
        gpu = detect_gpu()                       # mangoapp leaves CSV gpu blank → probe host
        if gpu:
            for s in sessions:
                if s["hw"].get("gpu", "?") in ("?", ""):
                    s["hw"]["gpu"] = gpu
    play_sessions = _group_play_sessions(sessions)
    hw = sessions[-1]["hw"] if sessions else {"cpu": "?", "gpu": "?"}
    return {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "hw": hw,
        "totals": {"sessions": len(sessions),
                   "hours": round(total_s / 3600, 1),
                   "samples": sum(s["samples"] for s in sessions)},
        "active_channel": active,
        "channels": chan_list,
        "settings": settings_data,
        "runtime": runtime,
        "runtimes": runtimes,
        "play_sessions": play_sessions,
        "sessions": sessions,
    }

_GPU = None
def detect_gpu():
    """MangoHud's gamescope/mangoapp path leaves the CSV gpu field blank, so when
    a capture has no GPU name, fall back to probing the host (the machine that
    generated the dashboard = the gaming rig). Cached. Returns '' if unknown."""
    global _GPU
    if _GPU is not None:
        return _GPU
    _GPU = ""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=4).stdout.strip()
        if out:
            _GPU = out.splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not _GPU:
        try:
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=4).stdout
            for line in out.splitlines():
                if re.search(r"VGA compatible|3D controller|Display controller", line, re.I):
                    m = re.search(r"\[([^\]]+)\]", line)        # e.g. [GeForce RTX 3090]
                    _GPU = (m.group(1) if m else line.split(":", 2)[-1]).strip()
                    break
        except (OSError, subprocess.SubprocessError):
            pass
    return _GPU

def _group_play_sessions(sessions):
    """Group captures that share one Game.log session (a single game launch) into
    play sessions, so the dashboard can lay their activity splits on one timeline.
    Captures with no matched session each become their own group."""
    groups = {}
    for i, s in enumerate(sessions):
        sess = s.get("session")
        key = sess["start"] if sess else "solo:" + s["start_iso"]
        g = groups.get(key)
        if not g:
            g = groups[key] = {
                "start": (sess["start"].replace(" ", "T") if sess else s["start_iso"]),
                "end": (sess["end"].replace(" ", "T") if sess else None),
                "build": s.get("build", ""), "region": s.get("region", ""),
                "caps": [],
            }
        g["caps"].append(i)
    out = []
    for g in sorted(groups.values(), key=lambda g: g["start"]):
        caps = [sessions[i] for i in g["caps"]]
        captured = sum(c["duration_s"] for c in caps)
        nev = sum(len(c.get("events", [])) for c in caps)
        bad = sum(1 for c in caps for e in c.get("events", []) if e.get("sev") == "bad")
        out.append({**g, "captured_s": round(captured, 1),
                    "n_captures": len(caps), "n_events": nev, "n_drops": bad})
    return out

def _sessions_from_csv(fh):
    """Rebuild (summary-only) session dicts from an exported CSV. No per-second
    series / histogram / event timeline — those aren't in the CSV."""
    out = []
    for r in csv.DictReader(fh):
        def f(k):
            try: return float(r.get(k, "") or 0)
            except ValueError: return 0.0
        dt = (r.get("When") or "").strip()
        out.append({
            "file": r.get("File", ""), "datetime": dt,
            "start_iso": (dt.replace(" ", "T") + ":00")[:19] if dt else "",
            "label": r.get("Zone", ""), "build": r.get("Build", ""),
            "region": r.get("Region", ""), "runtime_key": r.get("Runtime", ""),
            "duration_s": f("Duration_s"),
            "fps": {"avg": f("Avg"), "median": f("Median"), "min": f("Min"),
                    "max": f("Max"), "p1": f("1%low"), "p01": f("0.1%low")},
            "frametime": {"avg": f("Frametime_ms"), "p99": 0, "max": 0},
            "load": {"gpu_avg": f("GPU%"), "cpu_avg": f("CPU%")},
            "bottleneck": r.get("Bottleneck", ""),
            "temps": {"gpu_avg": f("GPUtemp"), "gpu_peak": f("GPUpeak"),
                      "cpu_avg": 0, "cpu_peak": 0},
            "vram": {"peak": f("VRAMpeak_GB"), "avg": 0},
            "ram_avg": 0, "samples": 0, "events": [], "session": None,
            "series": None, "hist": None, "hw": {"cpu": "?", "gpu": "?"},
        })
    return out

def import_data(paths):
    """Build a dashboard data dict from exported CSV/JSON file(s). JSON restores
    everything; CSV gives a summary view. Multiple files merge into one dashboard."""
    sessions, src = [], []
    for p in paths:
        p = os.path.expanduser(p)
        try:
            with open(p, encoding="utf-8") as fh:
                if fh.read(64).lstrip().startswith("{"):       # JSON
                    fh.seek(0)
                    sessions += json.load(fh).get("sessions", [])
                else:                                          # CSV
                    fh.seek(0)
                    sessions += _sessions_from_csv(fh)
            src.append(os.path.basename(p))
        except (OSError, ValueError) as e:
            print(f"  ! skipped {p}: {e}")
    sessions.sort(key=lambda s: s.get("datetime", ""))
    hw = next((s["hw"] for s in sessions if s.get("hw", {}).get("cpu", "?") != "?"),
              {"cpu": "?", "gpu": "?"})
    runtimes = {k: {"chips": []} for k in
                {s.get("runtime_key") for s in sessions if s.get("runtime_key")}}
    return {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "hw": hw,
        "totals": {"sessions": len(sessions),
                   "hours": round(sum(s.get("duration_s", 0) for s in sessions) / 3600, 1),
                   "samples": sum(s.get("samples", 0) for s in sessions)},
        "active_channel": "", "channels": [], "settings": {},
        "runtime": None, "runtimes": runtimes,
        "play_sessions": _group_play_sessions(sessions),
        "sessions": sessions, "imported": ", ".join(src),
    }

# ── HTML generation ──────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
__CHARTJS__
__HTML2CANVAS__
<style>
:root{--bg:#0e0f13;--card:#171922;--card2:#1e212c;--fg:#e6e8ef;--mut:#8b90a0;
--accent:#7c5cff;--gpu:#4fb0ff;--cpu:#ff7a59;--good:#46d18a;--warn:#ffcf5c;--bad:#ff5d6c;--line:#2a2e3b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 'JetBrainsMono Nerd Font','JetBrains Mono',ui-monospace,monospace}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 2px}.sub{color:var(--mut);font-size:12px;margin-bottom:20px}
.hwbar{display:flex;flex-wrap:wrap;gap:18px;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:14px 18px;margin-bottom:18px}
.hwbar b{color:var(--accent)}.hwbar span{color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.stat .k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.stat .v{font-size:24px;font-weight:600;margin-top:4px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:18px}
.panel h2{font-size:14px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:500;cursor:pointer;user-select:none}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--card2)}
tbody tr.sel{background:#23263340;outline:1px solid var(--accent)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.b-gpu{background:#1d3a55;color:var(--gpu)}.b-cpu{background:#4a2a1e;color:var(--cpu)}
.b-bal{background:#2a3a2e;color:var(--good)}
.charts{display:grid;grid-template-columns:2fr 1fr;gap:16px}
@media(max-width:760px){.charts{grid-template-columns:1fr}}
.cwrap{position:relative;height:240px}
.foot{color:var(--mut);font-size:11px;text-align:center;margin-top:24px}
.fps-good{color:var(--good)}.fps-mid{color:var(--warn)}.fps-bad{color:var(--bad)}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
.chip{background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:13px}
.chip b{color:var(--fg)}.chip span{color:var(--mut)}
.qbars{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px 18px}
.qbar{display:flex;align-items:center;gap:8px;font-size:12px}
.qbar .ql{flex:1;color:var(--mut)}.qbar .qv{width:78px;text-align:right;font-weight:600}
.qtrack{width:80px;height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.qfill{height:100%;border-radius:3px}
.events{margin-top:16px}
.events h3{font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin:0 0 8px}
.ev{font-size:13px;padding:5px 10px;border-left:3px solid var(--line);margin-bottom:4px;background:var(--card2);border-radius:0 6px 6px 0}
.ev-t{display:inline-block;min-width:64px;color:var(--mut);font-variant-numeric:tabular-nums}
.ev-bad{border-left-color:#ff5d6c}.ev-warn{border-left-color:#ffcf5c}.ev-info{border-left-color:#3a3f4d;opacity:.8}
.psess{margin-bottom:20px}
.phead{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--mut);margin-bottom:6px}
.ptrack{position:relative;height:50px;background:var(--card2);border:1px solid var(--line);border-radius:8px}
.pseg{position:absolute;top:3px;bottom:3px;border-radius:5px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column;justify-content:center;padding:2px 7px;font-size:11px;line-height:1.2;color:#0b0d12;font-weight:600;box-sizing:border-box;white-space:nowrap}
.pseg small{font-weight:500;opacity:.75}
.pseg:hover{outline:2px solid #ffffff88;z-index:5}
.ptick{position:absolute;top:0;bottom:0;width:2px;pointer-events:none;z-index:3}
.paxis{display:flex;justify-content:space-between;font-size:10px;color:var(--mut);margin-top:3px;font-variant-numeric:tabular-nums}
.toolbar{display:flex;gap:8px;margin:-4px 0 16px}
.btn{background:var(--card2);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:7px 14px;font-size:13px;cursor:pointer;font-family:inherit}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:default}
</style></head>
<body><div class="wrap">
<h1>🛰️ __TITLE__</h1>
<div class="sub" id="sub"></div>
<div class="toolbar">
  <button class="btn" id="expCsv" title="Session table → spreadsheet (summary)">⬇ CSV</button>
  <button class="btn" id="expJson" title="Full data → JSON (lossless; re-import for the exact dashboard)">⬇ JSON</button>
  <button class="btn" id="expImg" title="Full dashboard → image (everything, not just the screen)">🖼 JPEG</button>
</div>
<div class="hwbar" id="hwbar"></div>
<div class="grid" id="totals"></div>
<div class="panel" id="settingsPanel" style="display:none">
  <h2>Game settings · <span id="chanTag" style="color:var(--accent)"></span></h2>
  <div id="setDisplay" class="chips"></div>
  <div id="setQuality" class="qbars"></div>
</div>
<div class="panel" id="runtimePanel" style="display:none">
  <h2>Linux runtime <span style="text-transform:none;letter-spacing:0;color:var(--mut)">· wine / proton / dxvk / gamescope, from sc-launch.sh</span></h2>
  <div id="setRuntime" class="chips"></div>
</div>
<div class="panel"><h2>By zone / activity — avg FPS</h2><div class="cwrap" style="height:300px"><canvas id="cZone"></canvas></div></div>
<div class="panel" id="runtimeCmpPanel" style="display:none">
  <h2>By runtime — avg FPS <span style="text-transform:none;letter-spacing:0;color:var(--mut)">· same scenes, different Wine/Proton·gamescope·DXVK</span></h2>
  <div class="cwrap" style="height:240px"><canvas id="cRuntime"></canvas></div>
</div>
<div class="panel" id="playPanel" style="display:none">
  <h2>Play sessions <span style="text-transform:none;letter-spacing:0;color:var(--mut)">· activity splits on one timeline (click a segment for detail · vertical ticks = server events)</span></h2>
  <div id="playList"></div>
</div>
<div class="panel"><h2>Sessions</h2><table id="tbl"><thead></thead><tbody></tbody></table></div>
<div class="panel" id="detail" style="display:none">
  <h2 id="dtitle"></h2>
  <div class="grid" id="dstats"></div>
  <div class="charts">
    <div><div class="cwrap"><canvas id="cFps"></canvas></div></div>
    <div><div class="cwrap"><canvas id="cHist"></canvas></div></div>
  </div>
  <div id="devents" class="events"></div>
</div>
<div class="foot">Generated __GEN__ · sc-telemetry · data from MangoHud (hardware-only, no account/shard PII)</div>
</div>
<script>
const DATA = __DATA_JSON__;
const $=s=>document.querySelector(s);
const fpsClass=v=>v>=60?'fps-good':v>=40?'fps-mid':'fps-bad';
const bClass={'GPU-bound':'b-gpu','CPU / engine-bound':'b-cpu','Balanced':'b-bal'};
const fmtDur=s=>{const m=Math.floor(s/60),sec=Math.round(s%60);return m+'m '+sec+'s';};
const EVCOL={bad:'#ff5d6c',warn:'#ffcf5c',info:'#8b90a0'};   // server-event colours (used early)

// header
$('#sub').textContent = DATA.totals.sessions+' sessions'+(DATA.imported?' · imported from '+DATA.imported:' logged')+' · generated '+DATA.generated;
$('#hwbar').innerHTML = `<div><span>CPU</span> <b>${DATA.hw.cpu}</b></div>
  <div><span>GPU</span> <b>${DATA.hw.gpu}</b></div>
  <div><span>Kernel</span> <b>${DATA.hw.kernel||'?'}</b></div>
  <div><span>Sched</span> <b>${DATA.hw.scheduler||'?'}</b></div>`;
const avgAll = DATA.sessions.length ? (DATA.sessions.reduce((a,s)=>a+s.fps.avg,0)/DATA.sessions.length).toFixed(1):'—';
$('#totals').innerHTML = [
  ['Sessions',DATA.totals.sessions],['Hours logged',DATA.totals.hours],
  ['Avg FPS (all)',avgAll],['Samples',DATA.totals.samples.toLocaleString()]
].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

// settings panel (display + upscaling chips, quality bars)
if(DATA.settings && DATA.settings.display){
  const st=DATA.settings;
  $('#settingsPanel').style.display='block';
  $('#chanTag').textContent=[DATA.active_channel,(DATA.channels||[]).map(c=>c.channel).join(' / ')]
    .filter(Boolean).join('  ·  ');
  const chip=(k,v)=>`<div class="chip"><span>${k}</span> <b>${v}</b></div>`;
  $('#setDisplay').innerHTML=Object.entries({...st.display,...st.upscaling})
    .map(([k,v])=>chip(k,v)).join('');
  const qc=t=>t>=0.95?'#46d18a':t>=0.72?'#9ad14f':t>=0.45?'#ffcf5c':'#ff7a59';
  $('#setQuality').innerHTML=(st.quality||[]).map(q=>`<div class="qbar">
    <span class="ql">${q.name}</span>
    <span class="qtrack"><span class="qfill" style="width:${q.tier*100}%;background:${qc(q.tier)}"></span></span>
    <span class="qv" style="color:${qc(q.tier)}">${q.value}</span></div>`).join('');
}

// Linux runtime panel (runner / gamescope / dxvk / sync — perf-relevant on Linux)
if(DATA.runtime && (DATA.runtime.chips||[]).length){
  $('#runtimePanel').style.display='block';
  const kc={runner:'#7aa2ff',gamescope:'#c792ea',on:'#46d18a',tag:''};
  $('#setRuntime').innerHTML=DATA.runtime.chips.map(c=>{
    const col=kc[c.k];
    return `<div class="chip"${col?` style="border-color:${col}66"`:''}>`+
      `<b${col?` style="color:${col}"`:''}>${c.v}</b></div>`;}).join('');
}

// by-zone aggregate (groups captures by their auto/manual zone label)
let cZone;
(function(){
  const g={};
  DATA.sessions.forEach(s=>{const z=s.label||'untagged';(g[z]=g[z]||[]).push(s);});
  const zones=Object.keys(g);
  if(!zones.length)return;
  const avgs=zones.map(z=>+(g[z].reduce((a,s)=>a+s.fps.avg,0)/g[z].length).toFixed(1));
  const cnt=zones.map(z=>g[z].length);
  cZone=new Chart($('#cZone'),{type:'bar',
    data:{labels:zones.map((z,i)=>z+'  ('+cnt[i]+')'),
      datasets:[{label:'avg FPS',data:avgs,
        backgroundColor:avgs.map(v=>v>=60?'#46d18a':v>=40?'#ffcf5c':'#ff5d6c')}]},
    options:{indexAxis:'y',animation:false,responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},title:{display:true,text:'avg FPS per zone (capture count)',color:'#8b90a0'}},
      scales:{x:{grid:{color:'#2a2e3b'},ticks:{color:'#8b90a0'}},
        y:{grid:{color:'#2a2e3b'},ticks:{color:'#e6e8ef'}}}}});
})();

// by-runtime comparison — only meaningful once captures span ≥2 runtimes
let cRuntime;
(function(){
  const g={};
  DATA.sessions.forEach(s=>{const k=s.runtime_key;if(k)(g[k]=g[k]||[]).push(s);});
  const keys=Object.keys(g);
  if(keys.length<2)return;                       // nothing to compare yet
  $('#runtimeCmpPanel').style.display='block';
  const avgs=keys.map(k=>+(g[k].reduce((a,s)=>a+s.fps.avg,0)/g[k].length).toFixed(1));
  const lows=keys.map(k=>+(g[k].reduce((a,s)=>a+s.fps.p1,0)/g[k].length).toFixed(1));
  const cnt=keys.map(k=>g[k].length);
  cRuntime=new Chart($('#cRuntime'),{type:'bar',
    data:{labels:keys.map((k,i)=>k+'  ('+cnt[i]+')'),
      datasets:[{label:'avg FPS',data:avgs,backgroundColor:'#7aa2ff'},
                {label:'1% low',data:lows,backgroundColor:'#3a4a6b'}]},
    options:{indexAxis:'y',animation:false,responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#8b90a0'}},title:{display:true,text:'avg FPS & 1% low per runtime (capture count)',color:'#8b90a0'}},
      scales:{x:{grid:{color:'#2a2e3b'},ticks:{color:'#8b90a0'}},
        y:{grid:{color:'#2a2e3b'},ticks:{color:'#e6e8ef'}}}}});
})();

// play sessions — captures from one game launch laid out on a wall-clock timeline
(function(){
  const ps=DATA.play_sessions||[];
  const total=DATA.sessions.length;
  if(!ps.length || total<1) return;
  $('#playPanel').style.display='block';
  const fc=v=>v>=60?'#46d18a':v>=40?'#ffcf5c':'#ff5d6c';
  const T=s=>new Date(s).getTime();
  const clock=ms=>{const d=new Date(ms);return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);};
  $('#playList').innerHTML=ps.map(g=>{
    const caps=g.caps.map(i=>DATA.sessions[i]);
    let t0=g.start?T(g.start):0, t1=g.end?T(g.end):0;
    caps.forEach(c=>{const cs=T(c.start_iso),ce=cs+c.duration_s*1000;
      if(!t0||cs<t0)t0=cs; if(ce>t1)t1=ce;});
    const span=Math.max(1,t1-t0);
    let segs='',ticks='';
    caps.forEach(c=>{
      const cs=T(c.start_iso),ce=cs+c.duration_s*1000;
      const L=(cs-t0)/span*100, W=Math.max(1.5,(ce-cs)/span*100), idx=DATA.sessions.indexOf(c);
      segs+=`<div class="pseg" style="left:${L}%;width:${W}%;background:${fc(c.fps.avg)}" `+
        `onclick="showDetail(${idx})" title="${(c.label||c.file)} · avg ${c.fps.avg} fps · 1% ${c.fps.p1} · ${fmtDur(c.duration_s)}">`+
        `${c.label||c.file}<small>${c.fps.avg} fps</small></div>`;
      (c.events||[]).forEach(e=>{const x=(cs+e.at*1000-t0)/span*100;
        ticks+=`<div class="ptick" style="left:${x}%;background:${EVCOL[e.sev]}" title="+${Math.round(e.at)}s · ${e.detail}"></div>`;});
    });
    const drops=g.n_drops?`<span style="color:${EVCOL.bad}">⛔${g.n_drops} drop${g.n_drops>1?'s':''}</span> · `:'';
    return `<div class="psess">
      <div class="phead">
        <b style="color:var(--fg)">${clock(t0)}–${clock(t1)}</b>
        <span>${new Date(t0).toLocaleDateString()} · ${g.build||'?'}${g.region?' · '+g.region:''}</span>
        <span>${g.n_captures} split${g.n_captures>1?'s':''} · ${fmtDur(g.captured_s)} captured of ${fmtDur((t1-t0)/1000)} · ${drops}${g.n_events} server events</span>
      </div>
      <div class="ptrack">${segs}${ticks}</div>
      <div class="paxis"><span>${clock(t0)}</span><span>${clock(t1)}</span></div>
    </div>`;
  }).join('');
})();

// table
const COLS=[['datetime','When'],['label','Zone'],['runtime_key','Runtime'],['duration_s','Dur'],['fps.avg','Avg'],
['fps.p1','1% low'],['fps.p01','0.1% low'],['load.gpu_avg','GPU%'],['load.cpu_avg','CPU%'],
['bottleneck','Bottleneck'],['events','Server'],['temps.gpu_peak','GPU°'],['vram.peak','VRAM']];
const get=(o,p)=>p.split('.').reduce((a,k)=>a&&a[k],o);
let sortKey='datetime',sortDir=1;
$('#tbl thead').innerHTML='<tr>'+COLS.map(([k,l])=>`<th data-k="${k}">${l}</th>`).join('')+'</tr>';
function renderTable(){
  const rows=[...DATA.sessions].sort((a,b)=>{
    let x=get(a,sortKey),y=get(b,sortKey);
    if(sortKey==='events'){x=(a.events||[]).length;y=(b.events||[]).length;}
    if(typeof x==='string')return sortDir*x.localeCompare(y);
    return sortDir*((x||0)-(y||0));});
  $('#tbl tbody').innerHTML=rows.map(s=>{
    const i=DATA.sessions.indexOf(s);
    const cell=([k,_])=>{
      if(k==='label')return `<td>${s.label||'<span style=color:#555>—</span>'}</td>`;
      if(k==='duration_s')return `<td>${fmtDur(s.duration_s)}</td>`;
      if(k==='bottleneck')return `<td><span class="badge ${bClass[s.bottleneck]}">${s.bottleneck}</span></td>`;
      if(k==='events')return `<td>${evSummary(s.events)}</td>`;
      if(k==='fps.avg'||k==='fps.p1'||k==='fps.p01'){const v=get(s,k);return `<td class="${fpsClass(v)}">${v}</td>`;}
      if(k==='vram.peak')return `<td>${get(s,k)} GB</td>`;
      if(k==='temps.gpu_peak')return `<td>${get(s,k)}°</td>`;
      return `<td>${get(s,k)}</td>`;};
    return `<tr data-i="${i}">`+COLS.map(cell).join('')+'</tr>';}).join('');
  $('#tbl tbody').querySelectorAll('tr').forEach(tr=>tr.onclick=()=>showDetail(+tr.dataset.i,tr));
}
$('#tbl thead').querySelectorAll('th').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; sortDir=(sortKey===k)?-sortDir:1; sortKey=k; renderTable();});

// detail + charts
let cFps,cHist;
// vertical markers on the FPS chart at server-event times (by severity colour)
const eventPlugin={id:'events',afterDraw(c){
  const evs=c.$events; if(!evs||!evs.length)return;
  const {ctx,chartArea:{top,bottom,left,right}}=c, dur=c.$dur||1;
  ctx.save();
  evs.forEach(e=>{
    const x=left+Math.max(0,Math.min(1,e.at/dur))*(right-left);
    ctx.strokeStyle=EVCOL[e.sev]||'#8b90a0';ctx.lineWidth=1;ctx.globalAlpha=.65;
    ctx.setLineDash(e.sev==='info'?[3,3]:[]);
    ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();
  });
  ctx.restore();
}};
// compact per-session events summary (for the table cell)
function evSummary(evs){
  if(!evs||!evs.length)return '<span style="color:var(--mut)">—</span>';
  const n={bad:0,warn:0,info:0}; evs.forEach(e=>n[e.sev]++);
  const bits=[];
  if(n.bad) bits.push(`<span style="color:${EVCOL.bad}">⛔${n.bad}</span>`);
  if(n.warn)bits.push(`<span style="color:${EVCOL.warn}">↻${n.warn}</span>`);
  if(n.info)bits.push(`<span style="color:var(--mut)">·${n.info}</span>`);
  return bits.join(' ');
}

function showDetail(i,tr){
  document.querySelectorAll('#tbl tbody tr').forEach(r=>r.classList.remove('sel'));
  if(tr)tr.classList.add('sel');
  const s=DATA.sessions[i];
  $('#detail').style.display='block';
  $('#dtitle').textContent=`${s.datetime} · ${s.label||s.file} · ${fmtDur(s.duration_s)}`;
  $('#dstats').innerHTML=[
    ['Avg FPS',s.fps.avg,fpsClass(s.fps.avg)],['Median',s.fps.median,fpsClass(s.fps.median)],
    ['1% low',s.fps.p1,fpsClass(s.fps.p1)],['0.1% low',s.fps.p01,fpsClass(s.fps.p01)],
    ['Min / Max',s.fps.min+' / '+s.fps.max,''],['Frametime',s.frametime.avg+' ms',''],
    ['GPU load',s.load.gpu_avg+'%',''],['CPU load',s.load.cpu_avg+'%',''],
    ['GPU temp',s.temps.gpu_avg+'° (peak '+s.temps.gpu_peak+'°)',''],
    ['VRAM peak',s.vram.peak+' GB',''],
    ['Build',s.build||'?',''],['Region',s.region||'—','']
  ].map(([k,v,c])=>`<div class="stat"><div class="k">${k}</div><div class="v ${c}" style="font-size:${(''+v).length>10?'16px':'24px'}">${v}</div></div>`).join('');
  if(cFps)cFps.destroy(); if(cHist)cHist.destroy();
  const gx={grid:{color:'#2a2e3b'},ticks:{color:'#8b90a0'}};
  const hasSeries = s.series && s.series.fps && s.series.fps.length;
  document.querySelector('#detail .charts').style.display = hasSeries?'grid':'none';
  if(hasSeries){
    cFps=new Chart($('#cFps'),{type:'line',plugins:[eventPlugin],data:{labels:s.series.t,
      datasets:[
        {label:'FPS',data:s.series.fps,borderColor:'#7c5cff',backgroundColor:'#7c5cff22',
         borderWidth:1.4,pointRadius:0,fill:true,yAxisID:'y',tension:.2},
        {label:'Frametime (ms)',data:s.series.frametime,borderColor:'#ff7a59',
         borderWidth:1,pointRadius:0,yAxisID:'y1',tension:.2}]},
      options:{animation:false,responsive:true,maintainAspectRatio:false,
        plugins:{legend:{labels:{color:'#e6e8ef'}},title:{display:true,text:'FPS & frametime over session (s) — vertical lines = server events',color:'#8b90a0'}},
        scales:{x:gx,y:{...gx,position:'left',title:{display:true,text:'FPS',color:'#8b90a0'}},
          y1:{...gx,position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'ms',color:'#8b90a0'}}}}});
    cFps.$events=s.events||[]; cFps.$dur=s.duration_s||1; cFps.update();
    const labels=s.hist.edges.slice(0,-1).map((e,j)=>e+'–'+s.hist.edges[j+1]);
    cHist=new Chart($('#cHist'),{type:'bar',data:{labels,datasets:[{label:'frames',
      data:s.hist.counts,backgroundColor:s.hist.edges.slice(0,-1).map(e=>e>=60?'#46d18a':e>=40?'#ffcf5c':'#ff5d6c')}]},
      options:{animation:false,responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false},title:{display:true,text:'FPS distribution',color:'#8b90a0'}},
        scales:{x:gx,y:gx}}});
  }
  // events list under the charts (or a note when this is a summary-only import)
  const evs=s.events||[];
  $('#devents').innerHTML = evs.length
    ? '<h3>Server events</h3>'+evs.map(e=>`<div class="ev ev-${e.sev}"><span class="ev-t">+${fmtDur(e.at)}</span> ${e.detail}</div>`).join('')
    : (hasSeries ? '' : '<div class="ev ev-info">Summary import — per-second trace, histogram and event timeline aren’t carried in a CSV. Share/import a <b>JSON</b> export for the full detail view.</div>');
}
renderTable();
if(DATA.sessions.length)showDetail(DATA.sessions.length-1,$('#tbl tbody tr:last-child'));

// ── exports (client-side, offline) ──────────────────────────────────────────
function download(name,blob){const u=URL.createObjectURL(blob);const a=document.createElement('a');
  a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1500);}
const STAMP=DATA.generated.replace(/[^0-9]/g,'').slice(0,12);
function toCsv(){
  const g=(o,p)=>p.split('.').reduce((a,k)=>a&&a[k],o);
  const esc=v=>{v=(v==null?'':''+v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
  const cols=[['When','datetime'],['Zone','label'],['Build','build'],['Region','region'],
    ['Runtime','runtime_key'],['Duration_s','duration_s'],['Avg','fps.avg'],['Median','fps.median'],
    ['1%low','fps.p1'],['0.1%low','fps.p01'],['Min','fps.min'],['Max','fps.max'],
    ['Frametime_ms','frametime.avg'],['GPU%','load.gpu_avg'],['CPU%','load.cpu_avg'],
    ['Bottleneck','bottleneck'],['GPUtemp','temps.gpu_avg'],['GPUpeak','temps.gpu_peak'],
    ['VRAMpeak_GB','vram.peak'],['Events','__ev'],['ServerDrops','__drop'],['File','file']];
  const out=[cols.map(c=>c[0]).join(',')];
  DATA.sessions.forEach(s=>out.push(cols.map(([_,p])=>
    p==='__ev'?(s.events||[]).length:
    p==='__drop'?(s.events||[]).filter(e=>e.sev==='bad').length:
    esc(g(s,p))).join(',')));
  return out.join('\n');
}
$('#expCsv').onclick=()=>download(`sc-telemetry-${STAMP}.csv`,
  new Blob([toCsv()],{type:'text/csv'}));
$('#expJson').onclick=()=>download(`sc-telemetry-${STAMP}.json`,
  new Blob([JSON.stringify(DATA)],{type:'application/json'}));
$('#expImg').onclick=function(){
  if(typeof html2canvas==='undefined'){alert('Image library not loaded (offline?). A screenshot works too.');return;}
  const b=this, old=b.textContent; b.disabled=true; b.textContent='rendering…';
  html2canvas(document.querySelector('.wrap'),{backgroundColor:'#0e0f13',scale:2,useCORS:true,logging:false})
    .then(c=>c.toBlob(bl=>{download(`sc-telemetry-${STAMP}.jpg`,bl);b.disabled=false;b.textContent=old;},'image/jpeg',0.92))
    .catch(e=>{b.disabled=false;b.textContent=old;alert('Image export failed: '+e.message+'\nA screenshot works too.');});
};

// live-reload when served via --serve (no-op when opened as a file://)
if(location.protocol.startsWith('http')){
  let _tok=null;
  setInterval(async()=>{try{const t=await(await fetch('poll')).text();
    if(_tok===null)_tok=t;else if(t!==_tok)location.reload();}catch(e){}},3000);
}
</script></body></html>"""

def _resource(name):
    """Path to a bundled asset (works both from source and PyInstaller binary)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

_ASSETS = {}
def _script_tag(filename, cdn):
    """Inline a vendored JS lib for an offline, self-contained file; fall back to
    the CDN if the vendored copy isn't present."""
    if filename not in _ASSETS:
        try:
            with open(_resource(filename), encoding="utf-8") as fh:
                _ASSETS[filename] = "<script>" + fh.read() + "</script>"
        except OSError:
            _ASSETS[filename] = f'<script src="{cdn}"></script>'
    return _ASSETS[filename]

def render_html(data, title):
    return (HTML
            .replace("__CHARTJS__", _script_tag(
                "chart.min.js",
                "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"))
            .replace("__HTML2CANVAS__", _script_tag(
                "html2canvas.min.js",
                "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"))
            .replace("__DATA_JSON__", json.dumps(data))
            .replace("__TITLE__", title)
            .replace("__GEN__", data["generated"]))

def render(data, out, title):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(render_html(data, title), encoding="utf-8")

def _logs_token(logs_dir):
    """Cheap change-token for the logs dir: capture/label count + total mtime.
    Changes whenever a capture or label is added/updated → drives live-reload."""
    t, n = 0.0, 0
    for f in glob.glob(os.path.join(logs_dir, "*")) + \
             glob.glob(os.path.join(logs_dir, "runtime", "*")):
        if f.rsplit(".", 1)[-1] in ("csv", "label", "txt", "json"):
            try: t += os.path.getmtime(f); n += 1
            except OSError: pass
    return f"{n}:{t:.0f}"

def serve(logs_dir, channels, sc_dir, title, port=8000):
    """Run a local dashboard that rebuilds on each request and tells the page to
    reload when new captures land. Bound to localhost only."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass                  # quiet
        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            p = self.path.split("?", 1)[0].rstrip("/")
            if p.endswith("poll"):
                self._send(200, "text/plain; charset=utf-8",
                           _logs_token(logs_dir).encode()); return
            if p in ("", "/"):
                try:
                    html = render_html(build_data(logs_dir, channels, sc_dir), title)
                    self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
                except Exception as e:
                    self._send(500, "text/plain; charset=utf-8",
                               f"build error: {e}".encode())
                return
            self._send(404, "text/plain; charset=utf-8", b"not found")

    srv = None
    for p in range(port, port + 10):                     # find a free port
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), H); port = p; break
        except OSError:
            continue
    if srv is None:
        print(f"  ! no free port in {port}–{port+9}"); return
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  ▶ dashboard live at {url}")
    print(f"    watching {logs_dir} — capture a session and the page refreshes itself.")
    print("    Ctrl-C to stop.\n")
    import webbrowser
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        srv.server_close()

def _write_label(csv_path, text):
    base = re.sub(r"\.csv$", "", csv_path)
    with open(base + ".label", "w") as fh:
        fh.write(text.strip() + "\n")

def tag_captures(logs_dir, files, label):
    """Manually label captures (overrides auto-tagging). With FILES + --label,
    set directly; with no FILES, interactively prompt for every untagged one."""
    if files:                                    # direct: --tag F1 F2 --label "..."
        if not label:
            print("  ! pass --label \"Your label\" with the file(s) to tag.")
            return
        for f in files:
            f = os.path.expanduser(f)
            if not os.path.exists(f):            # allow bare basenames in logs_dir
                alt = os.path.join(logs_dir, os.path.basename(f))
                f = alt if os.path.exists(alt) else f
            if not os.path.exists(f):
                print(f"  ! not found: {f}"); continue
            _write_label(f, label)
            print(f"  ✓ {os.path.basename(f)} → {label}")
        return
    # interactive: walk every untagged capture
    csvs = sorted(g for g in glob.glob(os.path.join(logs_dir, "*.csv"))
                  if "_summary" not in g)
    untagged = [c for c in csvs if not label_for(c)]
    if not untagged:
        print("  All captures already have a label. Nothing to tag.")
        return
    print(f"\n  {len(untagged)} untagged capture(s). Type a label for each "
          "(blank = skip, Ctrl-C = stop):\n")
    for c in untagged:
        try:
            s = analyze(c)
        except Exception:
            s = None
        ctx = f"avg {s['fps']['avg']} FPS · {fmt_dur(s['duration_s'])}" if s else "?"
        when = parse_dt(c).strftime("%Y-%m-%d %H:%M")
        try:
            lab = input(f"   {when}  ({ctx})\n     label: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if lab:
            _write_label(c, lab)
            print("     ✓ tagged\n")
        else:
            print("     – skipped\n")

def fmt_dur(s):
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec}s" if m else f"{sec}s"

def _shq(s):
    import shlex
    return shlex.quote(os.path.expanduser(s))

def _self_cmd():
    """How to re-invoke this tool from a shell script (binary vs source)."""
    if getattr(sys, "frozen", False):
        return _shq(sys.executable)
    return "python3 " + _shq(os.path.abspath(__file__))

def main():
    ap = argparse.ArgumentParser(description="Star Citizen MangoHud logs → dashboard.html")
    ap.add_argument("--logs", default=os.path.expanduser("~/sc-fps-logs"),
                    help="folder of MangoHud capture CSVs (default ~/sc-fps-logs)")
    ap.add_argument("--out", default="dist/dashboard.html")
    ap.add_argument("--title", default="Star Citizen — Performance Telemetry")
    ap.add_argument("--sc", default=None,
                    help="path to your StarCitizen folder (containing LIVE/PTU/...); "
                         "auto-detected & saved on first run if omitted")
    ap.add_argument("--no-sc", action="store_true",
                    help="skip Game.log + settings integration (MangoHud data only)")
    ap.add_argument("--setup-mangohud", action="store_true",
                    help="write a MangoHud logging config + print setup help, then exit")
    ap.add_argument("--stamp-runtime", action="store_true",
                    help="record a runtime snapshot (run from your launch script) then exit")
    ap.add_argument("--install-hook", action="store_true",
                    help="add --stamp-runtime to your LUG launch script (idempotent) then exit")
    ap.add_argument("--tag", nargs="*", metavar="CSV",
                    help="label captures: no args = interactively tag every untagged "
                         "capture; with CSV file(s) = apply --label to them, then exit")
    ap.add_argument("--label", help="label text to apply to the --tag CSV file(s)")
    ap.add_argument("--import", dest="imp", nargs="+", metavar="FILE",
                    help="build a dashboard from exported CSV/JSON file(s) instead of "
                         "local logs (JSON = full detail, CSV = summary; multiple files merge)")
    ap.add_argument("--serve", action="store_true",
                    help="run a live local dashboard (auto-refreshes as captures land) "
                         "instead of writing a file")
    ap.add_argument("--port", type=int, default=8000, help="port for --serve (default 8000)")
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    a = ap.parse_args()

    if a.tag is not None:
        tag_captures(a.logs, a.tag, a.label)
        return

    if a.imp:
        data = import_data(a.imp)
        out = a.out
        frozen = getattr(sys, "frozen", False)
        if frozen and out == "dist/dashboard.html":
            out = os.path.expanduser("~/sc-telemetry-imported.html")
        render(data, out, a.title)
        print(f"✓ imported {data['totals']['sessions']} session(s) from "
              f"{data['imported']} → {out}")
        if a.open or frozen:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(out))
        return

    if a.setup_mangohud:
        if mango: mango.setup()
        else: print("! mango helper module not found")
        return

    if a.stamp_runtime:                              # called from the launch script
        sc_dir = (scpaths.resolve(a.sc, interactive=False)[0] if scpaths else None)
        p = linuxenv.snapshot(sc_dir, a.logs) if linuxenv else None
        print(f"  stamped runtime → {p}" if p else "  (no Linux runtime to stamp)")
        return

    if a.install_hook:
        if not linuxenv:
            print("! linuxenv helper not found"); return
        sc_dir = scpaths.resolve(a.sc, interactive=False)[0] if scpaths else None
        script = linuxenv.find_launch_script(sc_dir)
        if not script:
            print("  ! No LUG launch script (sc-launch.sh) found. Add this line to "
                  "your launch script manually:\n      "
                  f"{_self_cmd()} --stamp-runtime"); return
        cmd = f"{_self_cmd()} --stamp-runtime --logs {_shq(a.logs)}"
        action, bak = linuxenv.install_hook(script, cmd)
        print(f"  ✓ {action} runtime-stamp hook in {script}\n    (backup: {os.path.basename(bak)})")
        print("  Each launch now records its runtime; captures are matched to it.")
        return

    channels, sc_dir = [], None
    if not a.no_sc and scpaths:
        sc_dir, channels = scpaths.resolve(a.sc, interactive=sys.stdin.isatty())
        if sc_dir:
            print(f"  SC install: {sc_dir}")
            print("  build channels: " + (", ".join(
                c['channel'] + ("" if c['has_logs'] else " (no logs)") for c in channels) or "none"))
        else:
            print("  (no SC folder found — using MangoHud data only; pass --sc PATH or --no-sc)")
    sc = sc_dir if not a.no_sc and scpaths else None
    if a.serve:
        serve(a.logs, channels, sc, a.title, a.port)
        return
    out = a.out
    frozen = getattr(sys, "frozen", False)   # running as a PyInstaller binary
    if frozen and out == "dist/dashboard.html":
        out = os.path.expanduser("~/sc-telemetry-dashboard.html")
    data = build_data(a.logs, channels, sc)
    render(data, out, a.title)
    print(f"✓ {data['totals']['sessions']} session(s), {data['totals']['hours']}h → {out}")
    if a.open or frozen:                     # bare binary run → open the result
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(out))

if __name__ == "__main__":
    main()
