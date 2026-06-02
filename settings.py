"""
settings.py — read a Star Citizen build's graphics settings from
`<channel>/user/client/0/Profiles/default/attributes.xml`.

attributes.xml is a flat list of <Attr name="..." value="..."/>. We map the
display + upscaling + SysSpec_* quality tiers into a friendly structure for the
dashboard. Mappings are best-effort (SC doesn't document the enums); unknowns
are shown as raw values rather than guessed.
"""
import re, os

ATTR = re.compile(r'<Attr name="([^"]+)" value="([^"]*)"')
QUAL = {"0": "Off", "1": "Low", "2": "Medium", "3": "High", "4": "Very High", "5": "Ultra"}
WMODE = {"0": "Windowed", "1": "Borderless", "2": "Fullscreen"}
# Upscaling technique enum (inferred): 0 off, 1 FSR, 2 DLSS, 3 XeSS, 4 TSR
UPTECH = {"0": "Off", "1": "FSR", "2": "DLSS", "3": "XeSS", "4": "TSR"}
ONOFF = {"0": "Off", "1": "On"}
# SysSpec_* key -> friendly label (order = display order)
QUALITY_KEYS = [
    ("SysSpec_TextureQuality", "Texture Quality"),
    ("SysSpec_TextureDetail", "Texture Detail"),
    ("SysSpec_TextureFiltering", "Texture Filtering"),
    ("SysSpec_TextureGround", "Ground Textures"),
    ("SysSpec_ShadowMaps", "Shadows"),
    ("SysSpec_ShadowScreenSpace", "Screen-Space Shadows"),
    ("SysSpec_PlanetVolumetricClouds", "Volumetric Clouds"),
    ("SysSpec_GasCloud", "Gas Clouds"),
    ("SysSpec_ObjectDetail", "Object Detail"),
    ("SysSpec_ObjectViewDistance", "Object View Distance"),
    ("SysSpec_Particles", "Particles"),
    ("SysSpec_WaterSim", "Water Simulation"),
    ("SysSpec_WaterCaustics", "Water Caustics"),
]

def read_attrs(path):
    try:
        with open(path, errors="ignore") as fh:
            return dict(ATTR.findall(fh.read()))
    except OSError:
        return {}

def parse_settings(attr_path):
    a = read_attrs(attr_path)
    if not a:
        return {}
    g = lambda k, d="": a.get(k, d)
    res = f"{g('Width','?')}×{g('Height','?')}"
    display = {
        "Resolution": res,
        "Window mode": WMODE.get(g("WindowMode"), g("WindowMode") or "?"),
        "VSync": ONOFF.get(g("VSync"), g("VSync") or "?"),
        "Field of view": (g("FOV")[:5] + "°") if g("FOV") else "?",
        "Motion blur": ONOFF.get(g("MotionBlur"), "?"),
        "Film grain": ONOFF.get(g("FilmGrain"), "?"),
    }
    if g("HDR") == "1":
        display["HDR"] = f"On · {g('HDRRefWhite','?')}-nit ref white · {g('HDRMaxBrightness','?')}-nit max"
    else:
        display["HDR"] = "Off"
    upscaling = {
        "Upscaling": ONOFF.get(g("Upscaling"), "?"),
        "Technique": UPTECH.get(g("UpscalingTechnique"), g("UpscalingTechnique") or "?"),
        "Model": g("UpscalingModel") or "?",
        "Sharpening": ONOFF.get(g("Sharpening"), g("Sharpening") or "?"),
    }
    quality = []
    for key, label in QUALITY_KEYS:
        if key in a:
            v = a[key]
            quality.append({"name": label, "value": QUAL.get(v, v), "tier": _tier(v)})
    return {"display": display, "upscaling": upscaling, "quality": quality}

def _tier(v):
    """0..4 -> 0..1 for a bar/colour in the UI."""
    try:
        return round(min(1.0, int(v) / 4.0), 2)
    except ValueError:
        return 0.0
