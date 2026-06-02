"""
mango.py — write a ready-to-use MangoHud logging config and print setup help.
Invoked via `sc-telemetry --setup-mangohud`.
"""
import os, shutil, datetime

CONF = """### sc-telemetry MangoHud config — HUD + FPS logging
## HUD
fps
fps_metrics=avg,0.01,0.001
frametime
frame_timing
gpu_stats
gpu_temp
gpu_core_clock
gpu_power
vram
cpu_stats
cpu_temp
cpu_power
ram
gpu_name
vulkan_driver
resolution
engine_version
wine
position=top-left
font_size=20
background_alpha=0.35
round_corners=6
toggle_hud=Shift_R+F12

## Logging — one timestamped CSV per capture into output_folder.
## Press toggle_logging to START, press again to STOP.
## log_interval ms: 100 = 10 Hz (~3.4 MB/hour). 0 = per-frame (precise lows, ~7x larger).
output_folder=__LOGS__
log_interval=100
toggle_logging=Shift_L+F2
"""

HELP = """
─────────────────────────────────────────────────────────────────────
 MangoHud configured for Star Citizen FPS logging.
─────────────────────────────────────────────────────────────────────
 Config : {conf}
 Logs   : {logs}/   (one CSV per capture)

 In-game:
   Shift_L + F2   start / stop a log capture
   Shift_R + F12  toggle the on-screen overlay

 Launch SC with MangoHud:
   • Native Wine/Proton:   MANGOHUD=1 %command%      (or your launch script)
   • Steam launch options: MANGOHUD=1 %command%
   • Under gamescope:      gamescope --mangoapp -- <game>
                           (use --mangoapp INSTEAD of MANGOHUD=1)

 Tip: capture per activity — start a log entering a Contested Zone, stop on
 exit, start again in Onyx, etc. Each capture becomes its own dashboard session,
 auto-tagged with zone + build from your Game.log.

 Then build the dashboard:   sc-telemetry
─────────────────────────────────────────────────────────────────────
"""

def setup(logs_dir=None):
    if os.name == "nt":
        print("\n  MangoHud is Linux-only. On Windows, capture frametimes with "
              "PresentMon or CapFrameX and point sc-telemetry at the CSVs, or run "
              "Star Citizen via Proton on Linux to use MangoHud.\n")
        return
    cfgdir = os.path.join(os.path.expanduser(
        os.environ.get("XDG_CONFIG_HOME", "~/.config")), "MangoHud")
    logs = os.path.expanduser(logs_dir or "~/sc-fps-logs")
    os.makedirs(cfgdir, exist_ok=True)
    os.makedirs(logs, exist_ok=True)
    path = os.path.join(cfgdir, "MangoHud.conf")
    if os.path.exists(path):
        bak = path + ".bak-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(path, bak)
        print(f"  (backed up existing config → {os.path.basename(bak)})")
    with open(path, "w") as fh:
        fh.write(CONF.replace("__LOGS__", logs))
    if not shutil.which("mangohud"):
        print("  ! mangohud not found on PATH — install it (e.g. `sudo pacman -S "
              "mangohud lib32-mangohud` / `apt install mangohud`).")
    print(HELP.format(conf=path, logs=logs))
