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
import csv, json, os, glob, argparse, statistics, datetime, re, sys
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
        "runtime_key": rt_key,
        "build": enr.get("build", ""),
        "region": enr.get("region") or "",
        "datetime": parse_dt(path).strftime("%Y-%m-%d %H:%M"),
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
        "sessions": sessions,
    }

# ── HTML generation ──────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
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
</style></head>
<body><div class="wrap">
<h1>🛰️ __TITLE__</h1>
<div class="sub" id="sub"></div>
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
<div class="panel"><h2>Sessions</h2><table id="tbl"><thead></thead><tbody></tbody></table></div>
<div class="panel" id="detail" style="display:none">
  <h2 id="dtitle"></h2>
  <div class="grid" id="dstats"></div>
  <div class="charts">
    <div><div class="cwrap"><canvas id="cFps"></canvas></div></div>
    <div><div class="cwrap"><canvas id="cHist"></canvas></div></div>
  </div>
</div>
<div class="foot">Generated __GEN__ · sc-telemetry · data from MangoHud (hardware-only, no account/shard PII)</div>
</div>
<script>
const DATA = __DATA_JSON__;
const $=s=>document.querySelector(s);
const fpsClass=v=>v>=60?'fps-good':v>=40?'fps-mid':'fps-bad';
const bClass={'GPU-bound':'b-gpu','CPU / engine-bound':'b-cpu','Balanced':'b-bal'};
const fmtDur=s=>{const m=Math.floor(s/60),sec=Math.round(s%60);return m+'m '+sec+'s';};

// header
$('#sub').textContent = DATA.totals.sessions+' sessions logged · generated '+DATA.generated;
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

// table
const COLS=[['datetime','When'],['label','Zone'],['runtime_key','Runtime'],['duration_s','Dur'],['fps.avg','Avg'],
['fps.p1','1% low'],['fps.p01','0.1% low'],['load.gpu_avg','GPU%'],['load.cpu_avg','CPU%'],
['bottleneck','Bottleneck'],['temps.gpu_peak','GPU°'],['vram.peak','VRAM']];
const get=(o,p)=>p.split('.').reduce((a,k)=>a&&a[k],o);
let sortKey='datetime',sortDir=1;
$('#tbl thead').innerHTML='<tr>'+COLS.map(([k,l])=>`<th data-k="${k}">${l}</th>`).join('')+'</tr>';
function renderTable(){
  const rows=[...DATA.sessions].sort((a,b)=>{
    let x=get(a,sortKey),y=get(b,sortKey);
    if(typeof x==='string')return sortDir*x.localeCompare(y);
    return sortDir*((x||0)-(y||0));});
  $('#tbl tbody').innerHTML=rows.map(s=>{
    const i=DATA.sessions.indexOf(s);
    const cell=([k,_])=>{
      if(k==='label')return `<td>${s.label||'<span style=color:#555>—</span>'}</td>`;
      if(k==='duration_s')return `<td>${fmtDur(s.duration_s)}</td>`;
      if(k==='bottleneck')return `<td><span class="badge ${bClass[s.bottleneck]}">${s.bottleneck}</span></td>`;
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
  cFps=new Chart($('#cFps'),{type:'line',data:{labels:s.series.t,
    datasets:[
      {label:'FPS',data:s.series.fps,borderColor:'#7c5cff',backgroundColor:'#7c5cff22',
       borderWidth:1.4,pointRadius:0,fill:true,yAxisID:'y',tension:.2},
      {label:'Frametime (ms)',data:s.series.frametime,borderColor:'#ff7a59',
       borderWidth:1,pointRadius:0,yAxisID:'y1',tension:.2}]},
    options:{animation:false,responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#e6e8ef'}},title:{display:true,text:'FPS & frametime over session (s)',color:'#8b90a0'}},
      scales:{x:gx,y:{...gx,position:'left',title:{display:true,text:'FPS',color:'#8b90a0'}},
        y1:{...gx,position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'ms',color:'#8b90a0'}}}}});
  const labels=s.hist.edges.slice(0,-1).map((e,j)=>e+'–'+s.hist.edges[j+1]);
  cHist=new Chart($('#cHist'),{type:'bar',data:{labels,datasets:[{label:'frames',
    data:s.hist.counts,backgroundColor:s.hist.edges.slice(0,-1).map(e=>e>=60?'#46d18a':e>=40?'#ffcf5c':'#ff5d6c')}]},
    options:{animation:false,responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},title:{display:true,text:'FPS distribution',color:'#8b90a0'}},
      scales:{x:gx,y:gx}}});
}
renderTable();
if(DATA.sessions.length)showDetail(DATA.sessions.length-1,$('#tbl tbody tr:last-child'));
</script></body></html>"""

def render(data, out, title):
    html = (HTML.replace("__DATA_JSON__", json.dumps(data))
                .replace("__TITLE__", title)
                .replace("__GEN__", data["generated"]))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html, encoding="utf-8")

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
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    a = ap.parse_args()

    if a.tag is not None:
        tag_captures(a.logs, a.tag, a.label)
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
    out = a.out
    frozen = getattr(sys, "frozen", False)   # running as a PyInstaller binary
    if frozen and out == "dist/dashboard.html":
        out = os.path.expanduser("~/sc-telemetry-dashboard.html")
    data = build_data(a.logs, channels, sc_dir if not a.no_sc and scpaths else None)
    render(data, out, a.title)
    print(f"✓ {data['totals']['sessions']} session(s), {data['totals']['hours']}h → {out}")
    if a.open or frozen:                     # bare binary run → open the result
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(out))

if __name__ == "__main__":
    main()
