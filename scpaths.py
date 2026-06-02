"""
scpaths.py — locate the Star Citizen install, enumerate build channels, and
persist the choice (first-run config).

A channel is a folder under the StarCitizen install dir named LIVE / PTU / EPTU
/ HOTFIX / EVOCATI / TECH-PREVIEW that contains Game.log and/or an attributes.xml.
Players often rename LIVE -> HOTFIX/PTU between patches to avoid re-downloading,
so we detect by folder name AND content, and report whichever channels exist.
"""
import os, json, glob, datetime

CHANNELS = ["LIVE", "PTU", "EPTU", "HOTFIX", "EVOCATI", "TECH-PREVIEW"]
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")), "sc-telemetry", "config.json")

def _attr(channel_dir):
    return os.path.join(channel_dir, "user", "client", "0", "Profiles",
                        "default", "attributes.xml")

def channels_in(sc_dir):
    """List build channels found directly under sc_dir (the 'StarCitizen' folder)."""
    found = []
    if not sc_dir or not os.path.isdir(sc_dir):
        return found
    for name in sorted(os.listdir(sc_dir)):
        d = os.path.join(sc_dir, name)
        if not os.path.isdir(d):
            continue
        gamelog = os.path.join(d, "Game.log")
        attr = _attr(d)
        if name.upper() in CHANNELS or os.path.exists(gamelog) or os.path.exists(attr):
            if os.path.exists(gamelog) or os.path.exists(attr):
                mt = max([os.path.getmtime(p) for p in (gamelog, attr)
                          if os.path.exists(p)] or [0])
                found.append({
                    "channel": name.upper(),
                    "dir": d,
                    "attr": attr if os.path.exists(attr) else None,
                    "has_logs": os.path.exists(gamelog),
                    "last_used": datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M") if mt else "",
                    "_mt": mt,
                })
    return found

# Common install locations to probe. Bounded globs only (no recursive ** — that
# would scan whole drives). RSI suffix below each base.
_RSI = "Roberts Space Industries/StarCitizen"
def _candidates():
    home = os.path.expanduser("~")
    return [
        f"{home}/Games/star-citizen/drive_c/Program Files/{_RSI}",
        f"{home}/Games/*/drive_c/Program Files/{_RSI}",
        f"{home}/.local/share/Steam/steamapps/compatdata/*/pfx/drive_c/Program Files/{_RSI}",
        f"{home}/.steam/steam/steamapps/compatdata/*/pfx/drive_c/Program Files/{_RSI}",
        f"/mnt/*/{_RSI}", f"/mnt/*/*/{_RSI}",
        f"/mnt/*/SteamLibrary/steamapps/compatdata/*/pfx/drive_c/Program Files/{_RSI}",
        f"/run/media/*/*/{_RSI}",
        f"C:/Program Files/{_RSI}", f"D:/Program Files/{_RSI}",
        f"{home}/AppData/Local/Star Citizen/StarCitizen",
    ]

def autodetect():
    """First candidate path that actually contains build channels (short-circuits)."""
    for pat in _candidates():
        for m in glob.glob(pat):          # non-recursive, bounded depth
            if os.path.isdir(m) and channels_in(m):
                return m
    return None

def load_config():
    try:
        with open(CONFIG) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as fh:
        json.dump(cfg, fh, indent=2)

def resolve(sc_dir_arg=None, interactive=True):
    """Return (sc_dir, channels). Order: explicit arg > config > autodetect > prompt."""
    if sc_dir_arg and channels_in(sc_dir_arg):
        save_config({**load_config(), "sc_dir": sc_dir_arg})
        return sc_dir_arg, channels_in(sc_dir_arg)
    cfg = load_config()
    if cfg.get("sc_dir") and channels_in(cfg["sc_dir"]):
        return cfg["sc_dir"], channels_in(cfg["sc_dir"])
    auto = autodetect()
    if auto:
        save_config({**cfg, "sc_dir": auto})
        return auto, channels_in(auto)
    if interactive:
        print("\n  Star Citizen install not auto-detected.")
        print("  Enter the path to your 'StarCitizen' folder (the one containing")
        print("  LIVE / PTU / HOTFIX / EVOCATI), or leave blank to skip:\n")
        try:
            p = input("  SC folder: ").strip().strip('"')
        except EOFError:
            p = ""
        if p and channels_in(p):
            save_config({**cfg, "sc_dir": p})
            return p, channels_in(p)
        if p:
            print(f"  ! No build channels found under: {p}")
    return None, []

def primary_channel(channels):
    """The most-recently-used channel (newest Game.log/attributes mtime)."""
    return max(channels, key=lambda c: c["_mt"]) if channels else None
