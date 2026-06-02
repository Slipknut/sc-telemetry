#!/usr/bin/env python3
"""Generate clearly-labelled DEMO MangoHud captures into samples/ so the
dashboard's multi-session views can be shown without weeks of real play.
These are SYNTHETIC — for UI demo only. Real dashboards use ~/sc-fps-logs/."""
import os, random, datetime
random.seed(42)
OUT = os.path.join(os.path.dirname(__file__), "samples")
os.makedirs(OUT, exist_ok=True)

COLS = ("fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,"
        "gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,"
        "swap_used,process_rss,cpu_mhz,elapsed")
META = ("os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
        "CachyOS,AMD Ryzen 7 9800X3D 8-Core Processor,NVIDIA GeForce RTX 3090,"
        "65405512,7.0.10-2-cachyos,,performance\n")

# (label, fps_mean, fps_jitter, gpu_load, cpu_load, minutes, dip_chance, vram)
SCEN = [
    ("Area18 — Contested Zone", 23, 5, 56, 46, 14, 0.04, 30.5),
    ("Orison",                  28, 6, 59, 41, 11, 0.03, 29.0),
    ("Lorville",                32, 6, 63, 39,  9, 0.03, 28.2),
    ("Daymar (surface)",        50, 9, 76, 34, 10, 0.02, 18.0),
    ("Onyx Facility",           74,10, 91, 36, 12, 0.02, 12.5),
    ("Pirate Swarm (AC)",       73,11, 95, 37,  7, 0.02, 11.8),
    ("Space / Quantum",         96,14, 81, 31,  8, 0.01,  9.5),
]
base = datetime.datetime(2026, 5, 28, 18, 0, 0)

def gen(label, mean, jit, gpu, cpu, mins, dip, vram, when):
    n = int(mins * 60 * 10)            # 10 Hz
    fps_series = []
    for i in range(n):
        f = random.gauss(mean, jit)
        if random.random() < dip:      # occasional hitch
            f = max(8, f * random.uniform(0.3, 0.6))
        f = max(6, f)
        fps_series.append(f)
    rows = []
    for i, f in enumerate(fps_series):
        ft = 1000.0 / f
        rows.append(",".join(str(round(x, 3)) for x in [
            f, ft, random.gauss(cpu, 6), 0,
            min(100, random.gauss(gpu, 5)),
            random.gauss(58, 2), random.gauss(52, 2),
            1950, 0, random.gauss(vram, 0.4), random.gauss(250, 30),
            random.gauss(19, 1), 0, 0, random.gauss(5200, 200),
            int(i * 1e8)]))           # elapsed ns @10Hz
    s = COLS + "\n" + "\n".join(rows) + "\n"
    sf = sorted(fps_series)
    def p(q): return sf[max(0, min(len(sf)-1, int(len(sf)*q)))]
    summ = ("0.1% Min FPS,1% Min FPS,97% Percentile FPS,Average FPS,GPU Load,"
            "CPU Load,Average Frame Time,Average GPU Temp,Average CPU Temp,"
            "Average VRAM Used,Average RAM Used,Average Swap Used,Peak GPU Load,"
            "Peak CPU Load,Peak GPU Temp,Peak CPU Temp,Peak VRAM Used,Peak RAM Used,Peak Swap Used\n"
            + ",".join(str(round(x, 3)) for x in [
                p(.001), p(.01), p(.97), sum(fps_series)/n, gpu, cpu,
                1000.0/(sum(fps_series)/n), 52, 58, vram, 19, 0,
                min(100, gpu+5), cpu+25, 54, 65, vram+0.4, 19.5, 0]) + "\n")
    stamp = when.strftime("%Y-%m-%d_%H-%M-%S")
    p0 = os.path.join(OUT, f"mangoapp_{stamp}")
    open(p0 + ".csv", "w").write(META + s)
    open(p0 + "_summary.csv", "w").write(summ)
    open(p0 + ".label", "w").write(label + "\n")

t = base
for sc in SCEN:
    gen(*sc, when=t)
    t += datetime.timedelta(hours=random.randint(20, 50))
print(f"✓ generated {len(SCEN)} demo captures in {OUT}")
