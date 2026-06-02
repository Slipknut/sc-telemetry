"""
linuxenv.py — read the Linux *runtime* params Star Citizen launches with:
Wine/Proton runner, gamescope, DXVK, esync/fsync, HDR, DLSS overrides.

On Linux these often explain FPS differences as much as in-game graphics
settings do (e.g. a Wine-TkG bump, ntsync vs fsync, gamescope cap), so they're
worth showing next to the settings panel. Source is the LUG launch script
(sc-launch.sh); we locate it from the install dir or LUG's usual prefixes.
"""
import os, re, glob

def find_launch_script(sc_dir=None):
    """sc_dir is .../<prefix>/drive_c/Program Files/Roberts Space Industries/
    StarCitizen — the launch script lives at the prefix root."""
    cands = []
    if sc_dir:
        pfx = sc_dir
        for _ in range(5):            # walk up to the WINEPREFIX root
            pfx = os.path.dirname(pfx)
            cands.append(os.path.join(pfx, "sc-launch.sh"))
    home = os.path.expanduser("~")
    cands += glob.glob(f"{home}/Games/star-citizen/sc-launch.sh")
    cands += glob.glob(f"{home}/Games/*/sc-launch.sh")
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None

def _runner(path):
    """.../runners/lug-wine-tkg-staging-experimental-wayland-git-11.9-1/bin
    → {'name','type','version','tags'}."""
    if not path:
        return None
    m = re.search(r"runners/([^/]+)", path)
    raw = m.group(1) if m else os.path.basename(os.path.dirname(path.rstrip("/")))
    low = raw.lower()
    if "proton" in low:
        typ = "Proton-GE" if "ge" in low else "Proton"
    elif "tkg" in low:
        typ = "Wine-TkG"
    elif "wine" in low:
        typ = "Wine"
    else:
        typ = raw
    ver = re.search(r"(\d+\.\d+(?:[.-]\d+)?)", raw)
    tags = [t for t in ("staging", "experimental", "wayland", "ntsync",
                        "fsync", "esync") if t in low]
    return {"name": raw, "type": typ,
            "version": ver.group(1) if ver else "", "tags": tags}

def parse(sc_dir=None, script=None):
    """Return a runtime dict (with a flat `chips` list for the UI), or None."""
    script = script or find_launch_script(sc_dir)
    if not script:
        return None
    try:
        with open(script, errors="ignore") as fh:
            txt = fh.read()
    except OSError:
        return None
    # strip comments so a commented-out export never wins
    body = "\n".join(l.split("#", 1)[0] for l in txt.splitlines())

    def val(key):
        m = re.search(rf'(?m)^\s*(?:export\s+)?{key}=["\']?([^"\'\n]+)', body)
        return m.group(1).strip() if m else None

    def on(key):
        v = val(key)
        return None if v is None else v.lower() in ("1", "true", "on", "yes")

    runner = _runner(val("wine_path") or val("PROTONPATH") or val("proton") or
                     val("WINE"))

    gs = None
    gm = re.search(r"(?m)^\s*gamescope\s+(.+?)(?:--\s|$)", body)
    use_gs = on("USE_GAMESCOPE")
    if gm and use_gs is not False:
        f = gm.group(1)
        def flag(rx):
            m = re.search(rx, f)
            return m.group(1) if m else None
        gs = {
            "w": flag(r"-W\s+(\d+)"), "h": flag(r"-H\s+(\d+)"),
            "refresh": flag(r"-r\s+(\d+)"),
            "hdr": "--hdr-enabled" in f,
            "fullscreen": bool(re.search(r"(?:^|\s)(?:-f|--fullscreen)(?:\s|$)", f)),
            "mangoapp": "--mangoapp" in f,
        }

    sync = {"esync": on("WINEESYNC"), "fsync": on("WINEFSYNC"),
            "ntsync": on("WINE_NTSYNC") or on("PROTON_USE_NTSYNC")}

    dlss = any(re.search(rf"(?m)^\s*(?:export\s+)?{k}", body) and
               (val(k) or "").lower() == "on"
               for k in re.findall(r"(DXVK_NVAPI_DRS_NGX_DLSS_\w*?_OVERRIDE)\b", body))
    preset = val("DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION")
    dxvk = {"hdr": on("DXVK_HDR"), "async": on("DXVK_ASYNC"),
            "frame_rate": val("DXVK_FRAME_RATE"),
            "dlss_overrides": dlss,
            "preset": preset.replace("RENDER_PRESET_", "") if preset else None}

    mangohud = ("mangoapp" if (gs and gs["mangoapp"])
                else "layer" if on("MANGOHUD") else None)

    # flat chips for the dashboard
    chips = []
    if runner:
        chips.append({"k": "runner", "v": (runner["type"] +
                      (f" {runner['version']}" if runner["version"] else "")).strip()})
        for t in runner["tags"]:
            if t not in ("esync", "fsync"):     # shown separately below
                chips.append({"k": "tag", "v": t})
    if gs:
        res = f"{gs['w']}×{gs['h']}" if gs["w"] and gs["h"] else "gamescope"
        cap = f" @{gs['refresh']}" if gs["refresh"] else ""
        chips.append({"k": "gamescope", "v": f"gamescope {res}{cap}"})
        if gs["fullscreen"]:
            chips.append({"k": "tag", "v": "fullscreen"})
    if dxvk["hdr"] or (gs and gs["hdr"]):
        chips.append({"k": "on", "v": "HDR"})
    for label, flagval in (("esync", sync["esync"]), ("fsync", sync["fsync"]),
                           ("ntsync", sync["ntsync"])):
        if flagval:
            chips.append({"k": "on", "v": label})
    if dxvk["async"]:
        chips.append({"k": "on", "v": "DXVK async"})
    if dxvk["frame_rate"]:
        chips.append({"k": "tag", "v": f"DXVK cap {dxvk['frame_rate']}"})
    if dxvk["dlss_overrides"]:
        chips.append({"k": "on", "v": "DLSS override" +
                      (f" · preset {dxvk['preset']}" if dxvk["preset"] else "")})
    if mangohud:
        chips.append({"k": "tag", "v": f"MangoHud ({mangohud})"})

    return {"source": script, "runner": runner, "gamescope": gs,
            "sync": sync, "dxvk": dxvk, "mangohud": mangohud, "chips": chips}

if __name__ == "__main__":     # quick manual check
    import json, sys
    print(json.dumps(parse(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
