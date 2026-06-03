"""
gamelog — correlate MangoHud captures with Star Citizen's Game.log / logbackups
to auto-tag each capture with build, server region, and zone/activity.

Join key is wall-clock time. Capture filenames are LOCAL time; Game.log lines
are UTC. The offset is self-derived per session (logbackup filename is local,
its first log line is UTC), so it's DST-proof — no hardcoded timezone.

Privacy: only build / region / zone are extracted. The player handle in
RequestLocationInventory lines is parsed for matching but never emitted.
"""
import os, re, glob, datetime

TS = re.compile(r"<(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})")
BAK = re.compile(r"Build\((\d+)\)\s+(\d{2})\s+(\w{3})\s+(\d{2})\s+\((\d{2})\s+(\d{2})\s+(\d{2})\)")
LOCINV = re.compile(r"RequestLocationInventory>.*?Location\[([^\]]+)\]")
SHARD = re.compile(r"Shard Id:\s*([a-z0-9_]+)", re.I)
CLIST = re.compile(r"Changelist:\s*(\d+)")
BRANCH = re.compile(r"Branch:\s*\S*?(\d+\.\d+\.\d+[\w.-]*)")
# server-health events
DISCONN = re.compile(r'<Channel Disconnected>\s*cause=(\d+)\s*reason="([^"]*)"')
REROUTE = "Local Route Guard - Server Rerouted"      # bursty → time-clustered below
MENU = "Loading screen for Frontend_Main"            # arrived back at the main menu
# disconnect reasons that mean *you* left (vs. the server dropping you)
_VOLUNTARY = re.compile(r"player requested|requested disconnect|ExitToMenu|"
                        r"disconnect light", re.I)
MON = {m: i for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
CITY = ["Lorville","New Babbage","NewBabbage","Area18","Area 18","Orison",
        "GrimHex","Grim Hex","microTech","Hurston","Crusader","ArcCorp",
        "Daymar","Yela","Cellin","Aberdeen","Magda","Seraphim"]
REGION = {"euw": "EU-West", "use": "US-East", "usw": "US-West", "aus": "Australia",
          "apse": "Asia-SE", "euc": "EU-Central"}

# first segment of a location id is the system+body code (Stanton1, Stanton4b,
# Pyro3, …) — informative as a system, but not the place name.
_BODY = re.compile(r"^(Stanton|Pyro|Nyx|Terra|Magnus|Castra|Odin)\w*$", re.I)
# short codes that show up in space/station/transit ids
_CODES = {"HUR": "Hurston", "ARC": "ArcCorp", "CRU": "Crusader", "MIC": "microTech",
          "JP": "Jump Point", "LEO": "orbit", "RR": ""}
# proper names that must not be camel-split or title-cased
_NAMES = {"lorville": "Lorville", "newbabbage": "New Babbage", "area18": "Area 18",
          "orison": "Orison", "grimhex": "GrimHex", "arccorp": "ArcCorp",
          "microtech": "microTech"}
# orbital rest stops: id is RR_<planet>_LEO (LEO = the planet's main orbital
# station; L1–L5 are Lagrange stations handled generically below).
_ORBITAL = {"HUR": "Everus Harbor", "ARC": "Baijini Point",
            "MIC": "Port Tressler", "CRU": "Seraphim Station"}

def _body_name(code):
    if code in _CODES and _CODES[code]:
        return _CODES[code]
    return "Pyro" if re.fullmatch(r"P[1-6]", code) else code

def _camel(s):
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)        # word→Word
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)      # ACRONYMWord → ACRONYM Word
    s = re.sub(r"(?<=[A-Za-z])(\d{2,})", r" \1", s)       # Area045 → Area 045
    return s

def _prettify(loc):                       # "Stanton4_NewBabbage" -> "New Babbage"
    toks = loc.split("_")
    system = None
    if toks and _BODY.match(toks[0]):
        system = re.match(r"[A-Za-z]+", toks.pop(0)).group(0).title()
    if "JP" in toks:                       # RR_JP_StantonPyro → "Stanton–Pyro Jump Point"
        pair = toks[toks.index("JP") + 1] if toks.index("JP") + 1 < len(toks) else ""
        sysn = re.findall(r"[A-Z][a-z]+", pair)
        return f"{sysn[0]}–{sysn[1]} Jump Point" if len(sysn) >= 2 else "Jump Point"
    if toks[:1] == ["RR"] and len(toks) >= 3:    # orbital / Lagrange stations
        body, slot = toks[1], toks[2]
        if slot == "LEO" and body in _ORBITAL:
            return _ORBITAL[body]                # RR_HUR_LEO → Everus Harbor
        if re.fullmatch(r"L\d", slot):
            return f"{_body_name(body)} {slot} Station"   # RR_HUR_L1 → Hurston L1 Station
    if "Outpost" in toks:                  # procedural outpost id → "<System> Outpost"
        return f"{system} Outpost".strip() if system else "Outpost"
    if len(toks) == 1 and toks[0].lower() in _NAMES:
        return _NAMES[toks[0].lower()]
    out = []
    for t in toks:
        if t in _CODES:
            if _CODES[t]:
                out.append(_CODES[t])
        elif t.lower() in _NAMES:
            out.append(_NAMES[t.lower()])
        elif re.fullmatch(r"P[1-6]", t):   # Pyro body shorthand
            out.append("Pyro")
        else:
            out.append(_camel(t))
    return re.sub(r"\s+", " ", " ".join(out)).strip() or loc

def _region(shard):
    if not shard or shard == "local_shard":
        return None
    m = re.match(r"pub_([a-z]+)", shard)
    if m:
        for k, v in REGION.items():
            if m.group(1).startswith(k):
                return v
    return shard

def _meta_from(path):
    """Build id + local session start from a BackupNameAttachment, looked for in
    the filename (logbackups) and, failing that, the first log line (the live
    Game.log carries it there: `<...Z> BackupNameAttachment=" Build(..) DD Mon YY
    (HH MM SS)"`)."""
    m = BAK.search(os.path.basename(path))
    if not m:
        try:
            with open(path, errors="ignore") as fh:
                m = BAK.search(fh.readline())
        except OSError:
            m = None
    if not m:
        return None, None
    start = datetime.datetime(2000 + int(m.group(4)), MON.get(m.group(3), 1),
                              int(m.group(2)), int(m.group(5)),
                              int(m.group(6)), int(m.group(7)))
    return m.group(1), start

def index_sessions(sc_dir):
    """Cheap index of session logs: build + local start/end, no full parse."""
    out = []
    paths = glob.glob(os.path.join(sc_dir, "logbackups", "*.log"))
    live = os.path.join(sc_dir, "Game.log")
    if os.path.exists(live):
        paths.append(live)
    for p in paths:
        build, start = _meta_from(p)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        if start is None:                  # no attachment found: fall back to mtime
            start = mtime
        end = mtime                        # last write ≈ session end (esp. live log)
        if end < start:
            end = start + datetime.timedelta(hours=6)
        out.append({"path": p, "build": build, "start": start, "end": end})
    return out

def _offset(path, local_start):
    """local_start (from filename) minus first UTC log line = tz offset."""
    try:
        with open(path, errors="ignore") as fh:
            for _ in range(400):
                line = fh.readline()
                if not line:
                    break
                m = TS.search(line)
                if m:
                    utc = datetime.datetime.fromisoformat(m.group(1) + "T" + m.group(2))
                    return local_start - utc
    except OSError:
        pass
    return datetime.timedelta(hours=0)

def enrich(capture_start_local, duration_s, sessions):
    """Return {build, branch, region, zone, events} for a capture, or {} if no
    match. `events` are server-health markers (disconnect/reroute/menu) that fall
    inside the capture, each with `at` = seconds into the capture."""
    end_local = capture_start_local + datetime.timedelta(seconds=duration_s)
    sess = next((s for s in sessions
                 if s["start"] <= capture_start_local <= s["end"] + datetime.timedelta(minutes=5)),
                None)
    if not sess:
        return {}
    off = _offset(sess["path"], sess["start"])
    win0 = (capture_start_local - off) - datetime.timedelta(minutes=2)
    ev0 = capture_start_local - off                  # event window = the capture itself
    win1 = (end_local - off)
    build, branch, shard = sess["build"], None, None
    loc_anchor, city_count = None, {}
    is_ac = False
    events, last_reroute = [], None

    def add(t, kind, sev, detail):
        if len(events) < 80:
            secs = round((t - ev0).total_seconds(), 1)
            events.append({"at": max(0.0, secs), "kind": kind, "sev": sev, "detail": detail})

    try:
        with open(sess["path"], errors="ignore") as fh:
            for line in fh:
                if branch is None:
                    b = BRANCH.search(line)
                    if b: branch = b.group(1)
                if build is None:
                    c = CLIST.search(line)
                    if c: build = c.group(1)
                if "fps_loadout" in line or "Arena Commander" in line or "Vanduul" in line:
                    is_ac = True
                ts = TS.search(line)
                if not ts:
                    continue
                t = datetime.datetime.fromisoformat(ts.group(1) + "T" + ts.group(2))
                sm = SHARD.search(line)
                if sm:
                    shard = sm.group(1)
                if t > win1:
                    # past the window; keep scanning only cheaply for build/branch already done
                    if build and branch is not None:
                        break
                    continue
                li = LOCINV.search(line)
                if li and t <= win1:
                    loc_anchor = (t, li.group(1))
                if win0 <= t <= win1:
                    for c in CITY:
                        if c in line:
                            city_count[c] = city_count.get(c, 0) + 1
                # server-health events (only within the capture itself)
                if ev0 <= t <= win1:
                    dm = DISCONN.search(line)
                    if dm:
                        cause, reason = dm.group(1), dm.group(2)
                        if _VOLUNTARY.search(reason):
                            add(t, "quit", "info", f"Left to menu ({reason})")
                        else:
                            add(t, "disconnect", "bad",
                                f"Server drop — {reason} (code {cause})")
                    elif REROUTE in line:
                        if last_reroute is None or (t - last_reroute).total_seconds() > 20:
                            add(t, "reroute", "warn", "Server reroute / recovery")
                        last_reroute = t
                    elif MENU in line:
                        add(t, "menu", "info", "Returned to main menu")
    except OSError:
        return {}
    # decide zone
    if shard == "local_shard" or (is_ac and not (shard or "").startswith("pub")):
        zone = "Arena Commander"
    elif loc_anchor:
        zone = _prettify(loc_anchor[1])
    elif city_count:
        zone = max(city_count, key=city_count.get).replace("NewBabbage", "New Babbage")
    elif (shard or "").startswith("pub"):
        zone = "Stanton (PU)"              # in a PU shard but no location line in window
    else:
        zone = ""                          # menu/launcher/loading — nothing to claim
    events.sort(key=lambda e: e["at"])
    deduped = []                                       # drop dupes logged on 2 channels
    for e in events:
        if deduped and deduped[-1]["detail"] == e["detail"] \
           and abs(deduped[-1]["at"] - e["at"]) <= 1.5:
            continue
        deduped.append(e)
    return {"build": (branch or "?") + (f" ({build})" if build else ""),
            "region": _region(shard), "zone": zone, "events": deduped,
            "session": {"start": sess["start"].strftime("%Y-%m-%d %H:%M:%S"),
                        "end": sess["end"].strftime("%Y-%m-%d %H:%M:%S")}}
