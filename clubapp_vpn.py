#!/usr/bin/env python3
"""Clubapp VPN - a desktop client for Aether.

Runs the selected Aether engine in the background, waits for its local
SOCKS5 proxy, and then routes traffic through it either as a Windows system
proxy or (VPN mode) as a full-system TUN tunnel via tun2socks + Wintun.
Channel: https://t.me/Clubapp8

The Home page offers three engines - Aether, Psiphon and Tor. All three are
driven by the same Aether core (aether 2.1+), which launches the Psiphon /
Tor helper binaries that live in the "pt" folder next to aether.exe.

Third-party binaries this app can drive:
  - Aether core        https://github.com/CluvexStudio/Aether
  - pt/ helpers        psiphon-tunnel-core.exe, lyrebird.exe (shipped in the Aether zip)
  - tun2socks + Wintun  https://github.com/xjasonlyu/tun2socks , https://www.wintun.net
"""
import atexit
import base64
import ctypes
import hashlib
import io
import json
import math
import os
import platform
import queue
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

# Pillow is optional but strongly recommended: when present, the sidebar
# icons, the connect button and the settings on/off switches are rendered
# anti-aliased (drawn 4x oversize, then downsampled) instead of with Tk
# Canvas's raw, jagged primitives, which is what made them look pixelated.
# Everything still works without Pillow - it just falls back to the old
# blocky drawing. Run "pip install Pillow" before building the installer.
try:
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

APP_NAME = "Clubapp VPN"
APP_VERSION = "2.8.8"
GITHUB_URL = "https://github.com/Mrmmd2004"
TELEGRAM_URL = "https://t.me/Clubapp8"
ANDROID_URL = "https://github.com/Mrmmd2004/ClubappVpnAndroid"
CORE_REPO = "https://github.com/CluvexStudio/Aether"
CORE_API = "https://api.github.com/repos/CluvexStudio/Aether/releases/latest"
TUN2SOCKS_URL = "https://github.com/xjasonlyu/tun2socks/releases"
WINTUN_URL = "https://www.wintun.net/"

FROZEN = getattr(sys, "frozen", False)
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
_APPDATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ClubappVPN")
# Portable mode: a "portable.txt" next to the exe keeps all settings in a "data"
# folder beside it (falls back to %APPDATA% if that folder isn't writable).
PORTABLE = os.path.isfile(os.path.join(APP_DIR, "portable.txt"))
DATA_DIR = os.path.join(APP_DIR, "data") if PORTABLE else _APPDATA_DIR
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    PORTABLE, DATA_DIR = False, _APPDATA_DIR
CORE_DIR = os.path.join(DATA_DIR, "core")
TUN_DIR = os.path.join(DATA_DIR, "tun2socks")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PROXY_BACKUP = os.path.join(DATA_DIR, "proxy_backup.json")
CORE_EXE = "aether.exe" if os.name == "nt" else "aether"
_EXT = ".exe" if os.name == "nt" else ""
PSIPHON_EXE = "psiphon-tunnel-core" + _EXT
LYREBIRD_EXE = "lyrebird" + _EXT
os.makedirs(DATA_DIR, exist_ok=True)

# Palette: deep blue into cyan (replaces the old indigo/violet look).
BG, PANEL, PANEL2, PANEL3, LINE = "#070b16", "#101830", "#16213f", "#1b2a4d", "#233257"
TEXT, MUTED, FAINT = "#f2f5ff", "#93a4d1", "#5c6c94"
BLUE, CYAN, AMBER, ROSE = "#3d7bff", "#22d3ee", "#ffb64d", "#ff6b81"
GREEN = "#22e39a"  # blue-to-green gradient accent (sidebar, connect ring, connected glow)
VIOLET = BLUE  # kept as an alias so nothing else in the file needs renaming
FONT = "Segoe UI"
FONT_MONO = "Consolas"

# Aether transports. "mim" (MASQUE-in-MASQUE) is a transport the core supports
# (--mim / --protocol mim); every id here is something --protocol understands
# (masque3/masque2 both map to "masque", HTTP/2 vs HTTP/3 is a separate switch).
ENGINES = [
    ("masque3", "aether", "Aether \u2014 MASQUE / HTTP-3 (recommended)"),
    ("masque2", "aether", "Aether \u2014 MASQUE / HTTP-2 (if UDP is blocked)"),
    ("wg", "aether", "Aether \u2014 WireGuard"),
    ("gool", "aether", "Aether \u2014 WARP-in-WARP (gool)"),
    ("mim", "aether", "Aether \u2014 MASQUE-in-MASQUE (mim)"),
]
ENGINE_KIND = {eid: kind for eid, kind, _ in ENGINES}
ENGINE_LABEL = {eid: label for eid, kind, label in ENGINES}

# The three big buttons on the Home page: id, name, caption, icon gradient.
FAMILIES = [
    ("aether", "Aether", "MASQUE \u00b7 WireGuard \u00b7 WARP-in-WARP \u00b7 MASQUE-in-MASQUE", (BLUE, GREEN)),
    ("psiphon", "Psiphon", "CDN-fronted tunnel that keeps working where others are blocked", (BLUE, CYAN)),
    ("tor", "Tor", "The Tor network, with bridges fetched automatically", ("#7c5cff", BLUE)),
]
FAMILY_IDS = [f[0] for f in FAMILIES]
# Engines that get a small button on the Home page; Tor (and everything else)
# is picked in Settings. Add "tor" here to put it on Home as well.
HOME_ENGINES = ["aether", "psiphon"]
FAMILY_NAME = {f[0]: f[1] for f in FAMILIES}

# How Tor / Psiphon are combined with the Aether (WARP) tunnel.
#   chain   --tor / --psiphon          aether -> warp -> tor/psiphon -> internet
#   reverse --tor-reverse / ...        the tunnel is dialled THROUGH tor/psiphon
#   only    --tor-only / ...           no tunnel at all, plain tor/psiphon
TOR_MODES = [
    ("chain", "Aether \u2192 WARP \u2192 Tor"),
    ("reverse", "Tor \u2192 WARP"),
    ("only", "Tor only (no tunnel)"),
]
PSIPHON_MODES = [
    ("chain", "Aether \u2192 WARP \u2192 Psiphon"),
    ("reverse", "Psiphon \u2192 WARP"),
    ("only", "Psiphon only (no tunnel)"),
]
MODE_HELP = {
    ("tor", "chain"): "Tor runs inside the WARP tunnel, so your network only sees WARP. "
                      "Best choice when Tor is blocked. Traffic comes out of a Tor exit. "
                      "Bridges \"Auto\" = plain Tor first, then bridges.",
    ("tor", "reverse"): "The WARP tunnel is dialled through Tor, so your network never sees WARP. "
                        "Tor carries TCP only, so this always uses MASQUE over HTTP/2.",
    ("tor", "only"): "Plain Tor, no tunnel. On a network that blocks Tor this only works "
                     "if bridges are reachable.",
    ("psiphon", "chain"): "Psiphon runs inside the WARP tunnel, so your network only sees WARP.",
    ("psiphon", "reverse"): "The WARP tunnel is dialled through Psiphon, so your network never sees WARP. "
                            "Psiphon carries TCP only, so this always uses MASQUE over HTTP/2.",
    ("psiphon", "only"): "Plain Psiphon, no tunnel.",
}
PSIPHON_SHAPES = [("auto", "Auto"),
                  ("cdn", "CDN fronting only"),
                  ("direct", "Direct (no fronting)")]
# Psiphon exit regions (ISO country codes). Shown as flag chips on Home and in Settings.
PSIPHON_REGIONS = [
    ("AT", "Austria"), ("BE", "Belgium"), ("BG", "Bulgaria"), ("BR", "Brazil"),
    ("CA", "Canada"), ("CH", "Switzerland"), ("CZ", "Czechia"), ("DE", "Germany"),
    ("DK", "Denmark"), ("EE", "Estonia"), ("ES", "Spain"), ("FI", "Finland"),
    ("FR", "France"), ("GB", "United Kingdom"), ("HU", "Hungary"), ("IE", "Ireland"),
    ("IN", "India"), ("IT", "Italy"), ("JP", "Japan"), ("LV", "Latvia"),
    ("NL", "Netherlands"), ("NO", "Norway"), ("PL", "Poland"), ("RO", "Romania"),
    ("RS", "Serbia"), ("SE", "Sweden"), ("SG", "Singapore"), ("SK", "Slovakia"),
    ("UA", "Ukraine"), ("US", "United States"),
]
PSIPHON_REGION_NAME = dict(PSIPHON_REGIONS)
TOR_BRIDGES = [("auto", "Auto"),
               ("force", "Use bridges at once"),
               ("off", "Never use bridges")]
TOR_RELAY_PORTS = [("web", "Web ports (80, 443)"), ("any", "Any port")]
TOR_RELAY_CHOICES = ["auto", "only", "off", "40", "80", "150"]
PERF_PROFILES = [("", "Auto-detect"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]

# All six noize profiles work on every Aether transport (the core just has a
# different default per transport), so offer the full list everywhere.
ALL_NOIZE = ["off", "light", "firewall", "balanced", "gfw", "aggressive"]
NOIZE = {eid: ALL_NOIZE for eid, kind, _ in ENGINES if kind == "aether"}
SCANS = ["turbo", "balanced", "thorough", "verified", "ironclad"]
IP_MODES = [("4", "IPv4"), ("6", "IPv6"), ("dual", "Both (dual)")]
LOG_LEVELS = ["error", "warn", "info", "debug", "trace"]
DEFAULTS = dict(family="aether", protocol="masque3", scan="balanced", noize="balanced", ip="4",
                port=10808, tor_port=10820, psiphon_port=10821,
                system_proxy=True, vpn_mode=True, autoconnect=False,
                core_path="", tun2socks_path="",
                peer="", extra="", exit_loc="", dns="",
                stats=False, quick_reconnect=False, log_level="info", http2=False,
                # --- Tor
                tor_mode="chain", tor_bridges="auto", tor_relays="auto", tor_relay_ports="web",
                tor_bridge_line="", tor_bridge_file="",
                # --- Psiphon
                psiphon_mode="chain", psiphon_shape="auto", psiphon_region="",
                psiphon_cdn_ips="", psiphon_cdn_sni="", psiphon_config="",
                # --- newer Aether options
                http_proxy="", upstream="", wiw_peers="", mim_peers="",
                fragment=False, ech="", no_quic_v2=False, no_data_check=False, perf="",
                routes_file="", route_block="", route_direct="")

LOGO_B64 = (
    "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"
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


# ---------------------------------------------------------------- helpers
def mix(a, b, t):
    """Blend colour a towards colour b by t (0..1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(ca[i] * (1 - t) + cb[i] * t) for i in range(3))


def draw_gradient(canvas, x0, y0, x1, y1, c1, c2, steps=64):
    """Paint a smooth horizontal gradient from colour c1 to c2 across the
    given rectangle, used for the dashboard's blue-to-green accent strip."""
    width = max(1, x1 - x0)
    step_w = max(1.0, width / steps)
    for i in range(steps):
        color = mix(c1, c2, i / max(1, steps - 1))
        sx0 = x0 + i * step_w
        canvas.create_rectangle(sx0, y0, sx0 + step_w + 1, y1, fill=color, outline="")


def rounded_rect(canvas, x0, y0, x1, y1, r, fill, outline="", width=0, tags=()):
    """Draw a filled rounded rectangle on a Canvas (used everywhere in the
    redesigned UI for cards, chips, the connect button glow, etc)."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, fill=fill, outline=outline or fill,
                                 width=width, smooth=True, tags=tags)


def draw_vgradient(canvas, x0, y0, x1, y1, c1, c2, steps=48):
    """Vertical counterpart of draw_gradient - top-to-bottom c1 -> c2, used for
    the sidebar's active-tab accent bar and small vertical accents."""
    height = max(1, y1 - y0)
    step_h = max(1.0, height / steps)
    for i in range(steps):
        color = mix(c1, c2, i / max(1, steps - 1))
        sy0 = y0 + i * step_h
        canvas.create_rectangle(x0, sy0, x1, sy0 + step_h + 1, fill=color, outline="")


# --------------------------------------------------------- anti-aliased icons
# Tk's Canvas has no anti-aliasing, so small circles/rings/glyphs drawn with
# create_oval/create_arc come out jagged and "pixelated" - most visible on
# the settings on/off switches and the connect button. When Pillow is
# available we instead render everything 4x oversize with ImageDraw (which
# *is* anti-aliased) and downsample with a high-quality filter, giving crisp
# edges at any DPI. hex_rgba()/aa_photo() are the two building blocks; every
# other AA_* function below just draws one shape into that buffer.
def hex_rgba(c, alpha=255):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def aa_photo(w, h, draw_fn, scale=4):
    """Render draw_fn(draw, W, H) - W/H already multiplied by `scale` - onto
    a transparent RGBA buffer and return a downsampled ImageTk.PhotoImage.
    Returns None (caller must fall back to plain Canvas drawing) if Pillow
    isn't installed."""
    if not HAVE_PIL:
        return None
    w, h = max(1, int(w)), max(1, int(h))
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), W, H)
    img = img.resize((w, h), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def aa_switch_draw(track_hex, on):
    def _draw(d, W, H):
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=hex_rgba(track_hex))
        knob_d = H - int(H * 0.22)
        pad = (H - knob_d) // 2
        x = (W - knob_d - pad) if on else pad
        d.ellipse([x, pad, x + knob_d, pad + knob_d], fill=(255, 255, 255, 255))
    return _draw


def aa_icon_home(d, W, H, col):
    lw = max(2, int(W * 0.09))
    d.line([(W * 0.12, H * 0.5), (W * 0.5, H * 0.14), (W * 0.88, H * 0.5)],
           fill=col, width=lw, joint="curve")
    d.rectangle([W * 0.24, H * 0.48, W * 0.76, H * 0.86], outline=col, width=lw)
    d.rectangle([W * 0.42, H * 0.60, W * 0.58, H * 0.86], fill=col)


def aa_icon_gear(d, W, H, col):
    cx, cy = W / 2, H / 2
    r_outer, r_body, r_hole = W * 0.40, W * 0.28, W * 0.14
    tooth = W * 0.15
    for i in range(8):
        ang = math.pi * 2 * i / 8
        tx, ty = cx + math.cos(ang) * r_outer, cy + math.sin(ang) * r_outer
        d.ellipse([tx - tooth / 2, ty - tooth / 2, tx + tooth / 2, ty + tooth / 2], fill=col)
    d.ellipse([cx - r_body, cy - r_body, cx + r_body, cy + r_body], fill=col)
    d.ellipse([cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole], fill=(0, 0, 0, 0))


def aa_icon_logs(d, W, H, col):
    lw = max(2, int(H * 0.10))
    for y, x1 in ((0.26, 0.86), (0.5, 0.86), (0.74, 0.62)):
        d.line([(W * 0.14, H * y), (W * x1, H * y)], fill=col, width=lw)


def aa_icon_info(d, W, H, col):
    lw = max(2, int(W * 0.09))
    d.ellipse([W * 0.08, H * 0.08, W * 0.92, H * 0.92], outline=col, width=lw)
    r = W * 0.07
    d.ellipse([W * 0.5 - r, H * 0.26 - r, W * 0.5 + r, H * 0.26 + r], fill=col)
    d.line([(W * 0.5, H * 0.42), (W * 0.5, H * 0.76)], fill=col, width=lw)


NAV_ICON_DRAW = {"Home": aa_icon_home, "Settings": aa_icon_gear, "Logs": aa_icon_logs, "About": aa_icon_info}


def aa_power_button(size, ring_hex, fill_hex, icon_hex, ring_w, filled_ratio=None, arc_start=None, arc_extent=None):
    """Draw the connect button (outer ring + inner disc + power glyph) into
    one anti-aliased image. filled_ratio (0..1, CONNECTED state only) draws a
    blue-to-green gradient ring instead of a flat one; arc_start/arc_extent
    (STARTING/RECONNECTING/STOPPING) draw a spinner arc on top of a dim ring."""
    def _draw(d, W, H):
        scale = W / size
        rw = ring_w * scale
        cx = cy = W / 2
        R = W / 2 - rw / 2
        if filled_ratio is not None:
            steps = 64
            for i in range(steps):
                a0 = -90 + i * (360.0 / steps)
                a1 = a0 + 360.0 / steps + 1
                color = mix(BLUE, GREEN, i / (steps - 1))
                d.arc([cx - R, cy - R, cx + R, cy + R], a0, a1, fill=hex_rgba(color), width=int(rw))
        elif arc_start is not None:
            d.arc([cx - R, cy - R, cx + R, cy + R], 0, 360, fill=hex_rgba(mix(ring_hex, "#070b16", .6)),
                  width=int(rw))
            d.arc([cx - R, cy - R, cx + R, cy + R], arc_start - 90, arc_start - 90 + arc_extent,
                  fill=hex_rgba(ring_hex), width=int(rw))
        else:
            d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=hex_rgba(ring_hex), width=int(rw))
        Ri = R - rw / 2
        d.ellipse([cx - Ri, cy - Ri, cx + Ri, cy + Ri], fill=hex_rgba(fill_hex))
        s = Ri * 0.42
        lw = max(3, int(W * 0.022))
        d.arc([cx - s, cy - s + 3 * scale, cx + s, cy + s + 3 * scale], 125, 55,
              fill=hex_rgba(icon_hex), width=lw)
        d.line([(cx, cy - s * 1.15), (cx, cy - s * 0.1)], fill=hex_rgba(icon_hex), width=lw)
    return aa_photo(size, size, _draw)


# ---------------------------------------------------------------- flags
# Windows' Segoe UI has no flag emoji (they show up as two letters), so the
# flags are drawn with Pillow like the other icons - no image files needed.
_RED, _WHITE, _BLUE, _NAVY = "#d52b1e", "#ffffff", "#0b4ea2", "#1c2d6b"
_GREEN_F, _YEL, _BLK = "#009a49", "#fcd116", "#111111"


def _fl_h(d, W, H, cols, ws=None):
    ws = ws or [1] * len(cols)
    tot, y = float(sum(ws)), 0.0
    for i, (c, w) in enumerate(zip(cols, ws)):
        y2 = H if i == len(cols) - 1 else y + H * w / tot
        d.rectangle([0, round(y), W, round(y2)], fill=c)
        y = y2


def _fl_v(d, W, H, cols, ws=None):
    ws = ws or [1] * len(cols)
    tot, x = float(sum(ws)), 0.0
    for i, (c, w) in enumerate(zip(cols, ws)):
        x2 = W if i == len(cols) - 1 else x + W * w / tot
        d.rectangle([round(x), 0, round(x2), H], fill=c)
        x = x2


def _fl_cross(d, W, H, bg, col, inner=None, cx=0.36, t=0.2):
    d.rectangle([0, 0, W, H], fill=bg)
    for colr, th in ((col, int(H * t)), (inner, int(H * t * 0.5))):
        if colr:
            bx = int(W * cx)
            d.rectangle([bx - th // 2, 0, bx + th // 2, H], fill=colr)
            d.rectangle([0, H // 2 - th // 2, W, H // 2 + th // 2], fill=colr)


def _fl_us(d, W, H):
    for i in range(13):
        d.rectangle([0, round(H * i / 13), W, round(H * (i + 1) / 13)], fill=_RED if i % 2 == 0 else _WHITE)
    cw, ch = int(W * 0.42), round(H * 7 / 13)
    d.rectangle([0, 0, cw, ch], fill=_NAVY)
    r = max(2, int(H * 0.03))
    for row in range(4):
        for col in range(5):
            x, y = cw * (col + 1) / 6, ch * (row + 1) / 5
            d.ellipse([x - r, y - r, x + r, y + r], fill=_WHITE)


def _fl_gb(d, W, H):
    d.rectangle([0, 0, W, H], fill=_NAVY)
    for colr, w in ((_WHITE, 0.22), (_RED, 0.08)):
        d.line([0, 0, W, H], fill=colr, width=int(H * w))
        d.line([0, H, W, 0], fill=colr, width=int(H * w))
    for colr, t in ((_WHITE, 0.36), (_RED, 0.2)):
        th = int(H * t)
        d.rectangle([W // 2 - th // 2, 0, W // 2 + th // 2, H], fill=colr)
        d.rectangle([0, H // 2 - th // 2, W, H // 2 + th // 2], fill=colr)


def _fl_jp(d, W, H):
    d.rectangle([0, 0, W, H], fill=_WHITE)
    r = H * 0.3
    d.ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill="#bc002d")


def _fl_br(d, W, H):
    d.rectangle([0, 0, W, H], fill="#009c3b")
    d.polygon([(W * .5, H * .1), (W * .9, H * .5), (W * .5, H * .9), (W * .1, H * .5)], fill="#ffdf00")
    r = H * 0.2
    d.ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill="#002776")


def _fl_ca(d, W, H):
    _fl_v(d, W, H, [_RED, _WHITE, _RED], [1, 2, 1])
    pts = [(0, -1), (.25, -.6), (.5, -.7), (.4, -.2), (.8, -.35), (.7, .05), (.95, .2), (.4, .45), (.45, .65),
           (.05, .55), (.05, .95), (-.05, .95), (-.05, .55), (-.45, .65), (-.4, .45), (-.95, .2), (-.7, .05),
           (-.8, -.35), (-.4, -.2), (-.5, -.7), (-.25, -.6)]
    s = H * 0.38
    d.polygon([(W / 2 + x * s, H / 2 + y * s) for x, y in pts], fill=_RED)


def _fl_ch(d, W, H):
    d.rectangle([0, 0, W, H], fill=_RED)
    cx, cy, a, b = W / 2, H / 2, H * 0.3, H * 0.1
    d.rectangle([cx - b, cy - a, cx + b, cy + a], fill=_WHITE)
    d.rectangle([cx - a, cy - b, cx + a, cy + b], fill=_WHITE)


def _fl_in(d, W, H):
    _fl_h(d, W, H, ["#ff9933", _WHITE, "#138808"])
    r = H * 0.14
    d.ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], outline="#000080", width=max(1, int(H * 0.035)))


def _fl_cz(d, W, H):
    _fl_h(d, W, H, [_WHITE, "#d7141a"])
    d.polygon([(0, 0), (W * .5, H / 2), (0, H)], fill="#11457e")


def _fl_es(d, W, H):
    _fl_h(d, W, H, ["#aa151b", "#f1bf00", "#aa151b"], [1, 2, 1])
    d.rectangle([W * .2, H * .38, W * .31, H * .62], fill="#a87a3b")


def _fl_rs(d, W, H):
    _fl_h(d, W, H, ["#c6363c", "#0c4076", _WHITE])
    d.rectangle([W * .2, H * .25, W * .34, H * .6], fill="#c6363c", outline=_WHITE, width=max(1, int(H * .03)))


def _fl_sk(d, W, H):
    _fl_h(d, W, H, [_WHITE, "#0b4ea2", "#ee1c25"])
    d.rectangle([W * .2, H * .22, W * .42, H * .72], fill="#ee1c25", outline=_WHITE, width=max(1, int(H * .03)))
    cx, cy = W * .31, H * .45
    d.rectangle([cx - H * .03, cy - H * .17, cx + H * .03, cy + H * .15], fill=_WHITE)
    d.rectangle([cx - H * .11, cy - H * .08, cx + H * .11, cy - H * .03], fill=_WHITE)
    d.polygon([(W * .21, H * .58), (cx, H * .5), (W * .41, H * .58), (W * .41, H * .7), (W * .21, H * .7)], fill=_BLUE)


def _fl_sg(d, W, H):
    _fl_h(d, W, H, ["#ef3340", _WHITE])
    cx, cy, r = W * .24, H * .26, H * .19
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_WHITE)
    d.ellipse([cx - r * .7, cy - r, cx + r * 1.3, cy + r], fill="#ef3340")
    for k in range(5):
        a = math.radians(-90 + 72 * k)
        x, y, s = W * .38 + math.cos(a) * H * .11, H * .26 + math.sin(a) * H * .11, max(1.5, H * .028)
        d.ellipse([x - s, y - s, x + s, y + s], fill=_WHITE)


FLAG_DRAW = {
    "AT": lambda d, W, H: _fl_h(d, W, H, ["#ed2939", _WHITE, "#ed2939"]),
    "BE": lambda d, W, H: _fl_v(d, W, H, [_BLK, "#fae042", "#ed2939"]),
    "BG": lambda d, W, H: _fl_h(d, W, H, [_WHITE, "#00966e", "#d62612"]),
    "BR": _fl_br, "CA": _fl_ca, "CH": _fl_ch, "CZ": _fl_cz,
    "DE": lambda d, W, H: _fl_h(d, W, H, [_BLK, "#dd0000", "#ffce00"]),
    "DK": lambda d, W, H: _fl_cross(d, W, H, "#c8102e", _WHITE, t=0.17),
    "EE": lambda d, W, H: _fl_h(d, W, H, ["#0072ce", _BLK, _WHITE]),
    "ES": _fl_es,
    "FI": lambda d, W, H: _fl_cross(d, W, H, _WHITE, "#003580"),
    "FR": lambda d, W, H: _fl_v(d, W, H, ["#0055a4", _WHITE, "#ef4135"]),
    "GB": _fl_gb,
    "HU": lambda d, W, H: _fl_h(d, W, H, ["#ce2939", _WHITE, "#477050"]),
    "IE": lambda d, W, H: _fl_v(d, W, H, ["#169b62", _WHITE, "#ff883e"]),
    "IN": _fl_in,
    "IT": lambda d, W, H: _fl_v(d, W, H, ["#009246", _WHITE, "#ce2b37"]),
    "JP": _fl_jp,
    "LV": lambda d, W, H: _fl_h(d, W, H, ["#9e3039", _WHITE, "#9e3039"], [2, 1, 2]),
    "NL": lambda d, W, H: _fl_h(d, W, H, ["#ae1c28", _WHITE, "#21468b"]),
    "NO": lambda d, W, H: _fl_cross(d, W, H, "#ba0c2f", _WHITE, inner="#00205b"),
    "PL": lambda d, W, H: _fl_h(d, W, H, [_WHITE, "#dc143c"]),
    "RO": lambda d, W, H: _fl_v(d, W, H, ["#002b7f", "#fcd116", "#ce1126"]),
    "RS": _fl_rs,
    "SE": lambda d, W, H: _fl_cross(d, W, H, "#006aa7", "#fecc00"),
    "SG": _fl_sg, "SK": _fl_sk,
    "UA": lambda d, W, H: _fl_h(d, W, H, ["#0057b7", "#ffd700"]),
    "US": _fl_us,
}


def flag_photo(code, w, h, scale=4):
    """Anti-aliased rounded flag as a PhotoImage (None without Pillow / unknown code)."""
    fn = FLAG_DRAW.get(code)
    if not HAVE_PIL or fn is None:
        return None
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    fn(ImageDraw.Draw(img), W, H)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=int(H * 0.14), fill=255)
    img.putalpha(mask)
    return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))


def fmt_time(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if cfg["protocol"] not in ENGINE_KIND:
        cfg["protocol"] = DEFAULTS["protocol"]
    if cfg["protocol"] in NOIZE and cfg["noize"] not in NOIZE[cfg["protocol"]]:
        cfg["noize"] = NOIZE[cfg["protocol"]][0]
    if cfg.get("scan") not in SCANS:
        cfg["scan"] = DEFAULTS["scan"]
    if cfg.get("ip") not in ("4", "6", "dual"):
        cfg["ip"] = "4"
    if cfg.get("log_level") not in LOG_LEVELS:
        cfg["log_level"] = "info"
    if cfg.get("family") not in FAMILY_IDS:
        cfg["family"] = "aether"
    if cfg.get("tor_mode") not in [m for m, _ in TOR_MODES]:
        cfg["tor_mode"] = "chain"
    if cfg.get("psiphon_mode") not in [m for m, _ in PSIPHON_MODES]:
        cfg["psiphon_mode"] = "chain"
    if cfg.get("psiphon_shape") not in [m for m, _ in PSIPHON_SHAPES]:
        cfg["psiphon_shape"] = "auto"
    cfg["psiphon_region"] = str(cfg.get("psiphon_region") or "").strip().upper()
    if cfg["psiphon_region"] not in PSIPHON_REGION_NAME:
        cfg["psiphon_region"] = ""
    if cfg.get("tor_bridges") not in [m for m, _ in TOR_BRIDGES]:
        cfg["tor_bridges"] = "auto"
    if cfg.get("tor_relay_ports") not in [m for m, _ in TOR_RELAY_PORTS]:
        cfg["tor_relay_ports"] = "web"
    if cfg.get("perf") not in [m for m, _ in PERF_PROFILES]:
        cfg["perf"] = ""
    for k in ("port", "tor_port", "psiphon_port"):
        if not isinstance(cfg.get(k), int) or not 1024 <= cfg[k] <= 65535:
            cfg[k] = DEFAULTS[k]
    return cfg


def save_settings(cfg):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def port_open(port, timeout=0.3):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


# ------------------------------------------------------- Windows system proxy
INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_NAMES = (("ProxyEnable", "REG_DWORD"), ("ProxyServer", "REG_SZ"), ("ProxyOverride", "REG_SZ"))


def _refresh_inet():
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)  # settings changed
        wininet.InternetSetOptionW(0, 37, 0, 0)  # refresh
    except Exception:
        pass


def enable_system_proxy(port):
    """Point the per-user Windows proxy at our SOCKS5 port (previous values are backed up)."""
    if winreg is None:
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        if not os.path.exists(PROXY_BACKUP):
            backup = {}
            for name, _ in PROXY_NAMES:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                    backup[name] = [value, kind]
                except OSError:
                    backup[name] = None
            with open(PROXY_BACKUP, "w", encoding="utf-8") as f:
                json.dump(backup, f)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "socks=127.0.0.1:%d" % port)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.*;10.*;192.168.*;<local>")
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
    _refresh_inet()
    return True


def restore_system_proxy():
    """Put the Windows proxy settings back the way they were before we changed them."""
    if winreg is None or not os.path.exists(PROXY_BACKUP):
        return
    try:
        with open(PROXY_BACKUP, "r", encoding="utf-8") as f:
            backup = json.load(f)
    except (OSError, ValueError):
        backup = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if not backup:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            for name, _ in PROXY_NAMES:
                old = backup.get(name)
                if old is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except OSError:
                        pass
                else:
                    winreg.SetValueEx(key, name, 0, old[1], old[0])
        os.remove(PROXY_BACKUP)
    except OSError:
        return
    _refresh_inet()


# ------------------------------------------------------------- core download
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ClubappVPN/" + APP_VERSION,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pick_asset(assets):
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        def fits(n):
            return "arm64" in n or "aarch64" in n
    elif machine in ("amd64", "x86_64", "x64"):
        def fits(n):
            return any(w in n for w in ("x86_64", "x86-64", "amd64", "x64"))
    else:
        def fits(n):
            return any(w in n for w in ("i686", "i386", "win32")) or (
                "x86" in n and "x86_64" not in n and "x86-64" not in n)
    cands = [a for a in assets if "windows" in a["name"].lower()
             and a["name"].lower().endswith((".zip", ".exe"))]
    for a in cands:
        if fits(a["name"].lower()):
            return a
    return cands[0] if len(cands) == 1 else None


def parse_sums(text, filename):
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    return None


def download_core(log):
    log("Checking the latest Aether release...")
    rel = json.loads(http_get(CORE_API))
    assets = rel.get("assets", [])
    asset = pick_asset(assets)
    if not asset:
        raise RuntimeError("No matching Windows build in the latest release. Download it manually from "
                           + CORE_REPO + "/releases and use Browse in Settings.")
    log("Downloading %s (%s)..." % (asset["name"], rel.get("tag_name", "?")))
    blob = http_get(asset["browser_download_url"], timeout=120)
    sums = next((a for a in assets if a["name"].lower().startswith("sha256sums")), None)
    if sums:
        expected = parse_sums(http_get(sums["browser_download_url"]).decode("utf-8", "replace"), asset["name"])
        if expected and hashlib.sha256(blob).hexdigest() != expected:
            raise RuntimeError("Checksum mismatch - download aborted.")
        log("Checksum verified." if expected else "No checksum listed for this file.")
    else:
        log("No checksum file published; skipping verification.")
    os.makedirs(CORE_DIR, exist_ok=True)
    if asset["name"].lower().endswith(".exe"):
        path = os.path.join(CORE_DIR, CORE_EXE)
        with open(path, "wb") as f:
            f.write(blob)
        return path
    root = os.path.realpath(CORE_DIR)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(CORE_DIR, name))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError("Unsafe path inside archive.")
        z.extractall(CORE_DIR)
    for folder, _, files in os.walk(CORE_DIR):
        if CORE_EXE in files:
            return os.path.join(folder, CORE_EXE)
    raise RuntimeError("%s was not found inside the archive." % CORE_EXE)


def find_core(cfg):
    for p in (cfg.get("core_path"), os.path.join(CORE_DIR, CORE_EXE),
              os.path.join(APP_DIR, CORE_EXE), os.path.join(APP_DIR, "core", CORE_EXE)):
        if p and os.path.isfile(p):
            return p
    return shutil.which("aether")


def find_tun2socks(cfg):
    # "tun2socks.exe" is the generic name a user might rename their download to;
    # "tun2socks-windows-amd64.exe" / "-arm64.exe" are the actual names the
    # upstream releases (and this app's bundle) ship under, so both must be checked.
    if os.name == "nt":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        names = ("tun2socks.exe", "tun2socks-windows-%s.exe" % arch, "tun2socks-windows-amd64.exe")
    else:
        names = ("tun2socks",)
    for p in (cfg.get("tun2socks_path"), *[os.path.join(TUN_DIR, n) for n in names],
              *[os.path.join(APP_DIR, n) for n in names]):
        if p and os.path.isfile(p):
            ensure_wintun_next_to(p)
            return p
    return shutil.which("tun2socks")


def ensure_wintun_next_to(tun2socks_path):
    """tun2socks loads wintun.dll from its own directory. If it's missing there
    (e.g. tun2socks_path points at a user-picked file outside the app folder),
    copy the bundled/known-good wintun.dll next to it so VPN mode doesn't fail
    with a cryptic 'wintun.dll not found' error."""
    if os.name != "nt":
        return
    target_dir = os.path.dirname(tun2socks_path)
    dest = os.path.join(target_dir, "wintun.dll")
    if os.path.isfile(dest):
        return
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    for src in (os.path.join(APP_DIR, "wintun.dll"),
                os.path.join(APP_DIR, "wintun", "bin", arch, "wintun.dll"),
                os.path.join(RES_DIR, "wintun.dll")):
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass
            return


def find_engine_exe(cfg):
    return find_core(cfg)


def find_pt_bin(cfg, name):
    """Locate a helper binary (psiphon-tunnel-core / lyrebird). The Aether zip
    ships them in a "pt" folder next to aether.exe, so look there first."""
    core = find_core(cfg)
    dirs = []
    if core:
        base = os.path.dirname(core)
        dirs += [os.path.join(base, "pt"), base]
    dirs += [os.path.join(CORE_DIR, "pt"), os.path.join(APP_DIR, "pt"), APP_DIR,
             os.path.join(RES_DIR, "pt")]
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def family_mode(cfg):
    """None for plain Aether, else "chain" / "reverse" / "only"."""
    fam = cfg.get("family", "aether")
    if fam == "tor":
        return cfg.get("tor_mode", "chain")
    if fam == "psiphon":
        return cfg.get("psiphon_mode", "chain")
    return None


def uses_tunnel(cfg):
    """True when an Aether (WARP) tunnel is part of the connection."""
    return family_mode(cfg) != "only"


def active_port(cfg):
    """The local SOCKS5 port that carries the traffic the user wants: with
    --tor / --psiphon the exit you asked for is on its own port, otherwise it
    is the main --bind port."""
    fam, mode = cfg.get("family", "aether"), family_mode(cfg)
    if fam == "tor" and mode == "chain":
        return cfg["tor_port"]
    if fam == "psiphon" and mode == "chain":
        return cfg["psiphon_port"]
    return cfg["port"]


def ports_needed(cfg):
    ports = [cfg["port"]]
    fam, mode = cfg.get("family", "aether"), family_mode(cfg)
    if fam == "tor" and mode != "only":
        ports.append(cfg["tor_port"])
    if fam == "psiphon" and mode != "only":
        ports.append(cfg["psiphon_port"])
    return ports


def start_timeout(cfg):
    return {"tor": 720, "psiphon": 420}.get(cfg.get("family"), 300)


def route_text(cfg):
    fam, mode = cfg.get("family", "aether"), family_mode(cfg)
    if fam == "aether":
        proto = ENGINE_LABEL[cfg["protocol"]].split(" (")[0].split(" \u2014 ")[-1]
        return "%s, %s scan" % (proto, cfg["scan"])
    name = FAMILY_NAME[fam]
    if fam == "psiphon" and cfg.get("psiphon_region"):
        name += " (%s)" % cfg["psiphon_region"]
    return {"chain": "Aether tunnel \u2192 " + name,
            "reverse": name + " \u2192 WARP",
            "only": name + " only"}[mode]


# ------------------------------------------------------------ SOCKS5 self-test
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def socks5_trace(port, timeout=15):
    """Fetch Cloudflare's trace page through the local SOCKS5 proxy (stdlib only)."""
    host = "www.cloudflare.com"
    t0 = time.time()
    s = socket.create_connection(("127.0.0.1", port), timeout)
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        if _recv_exact(s, 2) != b"\x05\x00":
            raise OSError("SOCKS5 handshake refused")
        hb = host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + (443).to_bytes(2, "big"))
        head = _recv_exact(s, 4)
        if head[1] != 0:
            raise OSError("SOCKS5 connect failed (code %d)" % head[1])
        if head[3] == 1:
            _recv_exact(s, 6)
        elif head[3] == 4:
            _recv_exact(s, 18)
        else:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        tls = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        tls.sendall(("GET /cdn-cgi/trace HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n"
                     "User-Agent: ClubappVPN\r\n\r\n" % host).encode())
        data = b""
        while len(data) < 65536:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    info = dict(re.findall(r"^(\w+)=(.*?)\r?$", data.decode("utf-8", "replace"), re.M))
    if "ip" not in info:
        raise OSError("unexpected reply from the test server")
    info["ms"] = int((time.time() - t0) * 1000)
    return info


# ------------------------------------------------------------- process cleanup
def kill_tree(proc):
    """Stop a process AND its children. On Windows p.terminate() is a hard
    TerminateProcess on aether.exe only, which orphans psiphon-tunnel-core.exe;
    the orphan keeps the Psiphon datastore locked and every later start fails
    with 'tryDatastoreOpenDB ... timeout'. taskkill /T takes the whole tree."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except OSError:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError:
        pass


def kill_orphans(names=(PSIPHON_EXE, LYREBIRD_EXE)):
    """Kill leftover helper processes from an earlier run (crash / force-close)
    that would still hold the Psiphon datastore lock. Returns True if any
    taskkill call reported success."""
    if os.name != "nt":
        return False
    killed = False
    for n in names:
        try:
            r = subprocess.run(["taskkill", "/F", "/T", "/IM", n],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               capture_output=True, timeout=10)
            killed = killed or r.returncode == 0
        except Exception:
            pass
    if killed:
        time.sleep(1.0)  # let Windows release the file lock
    return killed


# ------------------------------------------------------------- core process
def build_command(exe, cfg, fresh=False, psi_dir=None):
    """Turn the saved settings into (argv, environment, notes) for aether."""
    fam, mode = cfg.get("family", "aether"), family_mode(cfg)
    tunnel = mode != "only"
    notes = []
    env = os.environ.copy()
    env.update(AETHER_SOCKS="127.0.0.1:%d" % cfg["port"], NO_COLOR="1", TERM="dumb",
               AETHER_LOG_LEVEL=cfg.get("log_level", "info"))
    args = [exe]

    def val(key):
        return str(cfg.get(key, "") or "").strip()

    if tunnel:
        proto = cfg["protocol"]
        if mode == "reverse" and proto in ("wg", "gool", "mim"):
            notes.append("%s can't be dialled through %s (TCP only) - using MASQUE / HTTP-2 instead."
                         % (ENGINE_LABEL[proto].split(" \u2014 ")[-1], FAMILY_NAME[fam]))
            proto = "masque2"
        # masque3/masque2 are both AETHER_PROTOCOL=masque (the HTTP/2 vs HTTP/3
        # choice is AETHER_MASQUE_HTTP2); wg/gool/mim pass straight through.
        env.update(AETHER_PROTOCOL={"masque3": "masque", "masque2": "masque"}.get(proto, proto),
                   AETHER_SCAN=cfg["scan"], AETHER_NOIZE=cfg["noize"])
        if proto == "masque2" or cfg.get("http2"):
            env["AETHER_MASQUE_HTTP2"] = "1"
        if val("peer"):
            env["AETHER_PEER"] = val("peer")
        if val("exit_loc"):
            env["AETHER_EXIT_LOC"] = val("exit_loc")
        if val("dns"):
            env["AETHER_DNS"] = val("dns")
        if cfg.get("stats"):
            env["AETHER_STATS"] = "1"
        if cfg.get("quick_reconnect"):
            env["AETHER_QUICK_RECONNECT"] = "1"
        if cfg["ip"] == "dual":
            args.append("--dual")
        else:
            args.append("-4" if cfg["ip"] == "4" else "-6")
        if proto == "gool" and val("wiw_peers"):
            args += ["--wiw-peers", val("wiw_peers")]
        if proto == "mim" and val("mim_peers"):
            args += ["--mim-peers", val("mim_peers")]
        if cfg.get("fragment"):
            args.append("--fragment")
        if val("ech"):
            args += ["--ech", val("ech")]
        if cfg.get("no_quic_v2"):
            args.append("--no-quic-v2")
        if cfg.get("no_data_check"):
            args.append("--no-data-check")

    # options that make sense with or without a tunnel
    if val("http_proxy"):
        args += ["--http-proxy", val("http_proxy")]
    if val("upstream"):
        args += ["--upstream", val("upstream")]
    if val("perf"):
        args += ["--perf", val("perf")]
    if val("routes_file"):
        args += ["--routes", val("routes_file")]
    if val("route_block"):
        args += ["--route-block", val("route_block")]
    if val("route_direct"):
        args += ["--route-direct", val("route_direct")]

    if fam == "tor":
        args.append({"chain": "--tor", "reverse": "--tor-reverse", "only": "--tor-only"}[mode])
        if tunnel:
            args += ["--tor-bind", "127.0.0.1:%d" % cfg["tor_port"]]
        if cfg["tor_bridges"] == "force":
            args.append("--tor-bridges")
        elif cfg["tor_bridges"] == "off":
            args.append("--no-tor-bridges")
        for line in re.split(r"[;\n]+", val("tor_bridge_line")):
            if line.strip():
                args += ["--tor-bridge", line.strip()]
        if val("tor_bridge_file"):
            args += ["--tor-bridge-file", val("tor_bridge_file")]
        if val("tor_relays") and val("tor_relays") != "auto":
            args += ["--tor-relays", val("tor_relays")]
        if cfg["tor_relay_ports"] != "web":
            args += ["--tor-relay-ports", cfg["tor_relay_ports"]]
        lyre = find_pt_bin(cfg, LYREBIRD_EXE)
        if lyre:
            args += ["--tor-pt-dir", os.path.dirname(lyre)]
        else:
            notes.append("lyrebird (the obfs4 helper) was not found - Tor bridges that need it "
                         "won't work. Use Settings \u2192 Download / update core.")
    elif fam == "psiphon":
        args.append({"chain": "--psiphon", "reverse": "--psiphon-reverse",
                     "only": "--psiphon-only"}[mode])
        if tunnel:
            args += ["--psiphon-bind", "127.0.0.1:%d" % cfg["psiphon_port"]]
        if cfg["psiphon_shape"] != "auto":
            args += ["--psiphon-mode", cfg["psiphon_shape"]]
        if val("psiphon_region"):
            args += ["--psiphon-region", val("psiphon_region").upper()]
        if val("psiphon_cdn_ips"):
            args += ["--psiphon-cdn-ips", val("psiphon_cdn_ips")]
        if val("psiphon_cdn_sni"):
            args += ["--psiphon-cdn-sni", val("psiphon_cdn_sni")]
        if val("psiphon_config"):
            args += ["--psiphon-config", val("psiphon_config")]
        if psi_dir:
            # own datastore folder: never reuse the old (possibly locked / broken) one
            args += ["--psiphon-dir", psi_dir]
        pb = find_pt_bin(cfg, PSIPHON_EXE)
        if pb:
            args += ["--psiphon-bin", pb]

    if fresh and tunnel:
        # after a drop, never reuse the gateway that just failed: it is often
        # the very endpoint being throttled, so a fresh scan picks a new one
        args.append("--no-quick-reconnect")
        env.pop("AETHER_QUICK_RECONNECT", None)

    if val("extra"):
        args += [a.strip('"') for a in shlex.split(val("extra"), posix=False)]
    return args, env, notes


class Core:
    def __init__(self, on_output):
        self.on_output = on_output
        self.proc = None
        self.ds_fail = False   # Psiphon reported a datastore (lock) failure
        self.psi_slot = 0      # which psiphon-data folder to use; bumped after a failure

    def psi_dir(self):
        name = "psiphon-data" if self.psi_slot == 0 else "psiphon-data-%d" % self.psi_slot
        return os.path.join(DATA_DIR, name)

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, exe, cfg, fresh=False):
        if cfg.get("family") in ("psiphon", "tor") and kill_orphans():
            self.on_output("[note] stopped a leftover Psiphon/Tor helper from an earlier run.\n")
        self.ds_fail = False
        psi_dir = None
        if cfg.get("family") == "psiphon":
            psi_dir = self.psi_dir()
            try:
                os.makedirs(psi_dir, exist_ok=True)
                # drop datastores from earlier slots (best effort; locked ones stay)
                for d in os.listdir(DATA_DIR):
                    full = os.path.join(DATA_DIR, d)
                    if d.startswith("psiphon-data") and full != psi_dir and os.path.isdir(full):
                        shutil.rmtree(full, ignore_errors=True)
            except OSError:
                pass
        args, env, notes = build_command(exe, cfg, fresh=fresh, psi_dir=psi_dir)
        for n in notes:
            self.on_output("[note] " + n + "\n")
        self.proc = subprocess.Popen(
            args, cwd=DATA_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.on_output("$ " + " ".join(args) + "\n")
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        try:
            while True:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                txt = chunk.decode("utf-8", "replace")
                if "tryDatastoreOpenDB" in txt or "openDataStore" in txt:
                    self.ds_fail = True
                self.on_output(txt)
        except Exception:
            pass

    def send(self, text):
        if self.alive:
            try:
                self.proc.stdin.write((text + "\n").encode())
                self.proc.stdin.flush()
            except OSError:
                pass

    def stop(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        kill_tree(p)


# --------------------------------------------------------- VPN (TUN) mode
def is_admin():
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Re-launch this same exe/script elevated, then quit the current process."""
    params = " ".join('"%s"' % a for a in sys.argv[1:]) if not FROZEN else " ".join('"%s"' % a for a in sys.argv[1:])
    target = sys.executable
    script = None if FROZEN else os.path.abspath(__file__)
    args = params if FROZEN else ('"%s" %s' % (script, params))
    ctypes.windll.shell32.ShellExecuteW(None, "runas", target, args, None, 1)


class TunBridge:
    """Drives tun2socks to turn the local SOCKS5 proxy into a full-system VPN
    using a Wintun virtual adapter. Needs administrator rights, tun2socks.exe
    and wintun.dll next to it (see DOWNLOAD LINKS in the About tab)."""

    TUN_NAME = "ClubappTun"
    TUN_IP = "10.10.10.10"
    DNS = "1.1.1.1"

    def __init__(self, on_output):
        self.on_output = on_output
        self.proc = None
        self.route_added = False

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, tun2socks_exe, socks_port):
        # NOTE: this used to hand tun2socks a "-tun-post-up postup.bat" and let
        # tun2socks itself run the netsh commands once the adapter was ready.
        # On Windows that silently does nothing: tun2socks execs the command
        # it's given directly (no shell), and Windows can't CreateProcess a
        # .bat file that way (it needs cmd.exe to interpret it) - so the IP,
        # DNS and default route were never actually applied. tun2socks itself
        # started fine and looked "connected", which is why proxy mode worked
        # but full VPN mode silently never routed anything. Configuring the
        # adapter ourselves, straight from Python (the same way this class
        # already deletes the route in stop() below), sidesteps that.
        # This build of tun2socks parses flags with pflag/cobra (note the
        # "-d, --device" style in its own usage text), which only accepts a
        # single dash for the one-letter shorthand forms and requires "--"
        # for everything else - "-device"/"-loglevel" get misread as shorthand
        # clusters ("-l" + leftover text), which is exactly the "unknown
        # shorthand flag: 'l'" error. It also spells the level "warn", not
        # "warning". Older tun2socks builds used Go's stdlib flag package,
        # which treats "-name" and "--name" the same - hence the old code
        # working there but not here. "--" works on both.
        args = [tun2socks_exe, "--device", "tun://" + self.TUN_NAME,
                "--proxy", "socks5://127.0.0.1:%d" % socks_port,
                "--loglevel", "warn"]
        self.proc = subprocess.Popen(
            args, cwd=DATA_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._configure_adapter, daemon=True).start()

    def _pump(self):
        proc = self.proc
        try:
            while proc is not None and proc.poll() is None:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                self.on_output(chunk.decode("utf-8", "replace"))
        except Exception:
            pass

    def _netsh(self, *args):
        try:
            r = subprocess.run(
                ["netsh"] + list(args), capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.on_output("[tun] netsh %s failed to run: %s\n" % (" ".join(args), e))
            return False
        if r.returncode != 0:
            self.on_output("[tun] netsh %s -> %s\n" % (" ".join(args), (r.stdout or r.stderr or "").strip()))
            return False
        return True

    def _adapter_present(self):
        try:
            r = subprocess.run(
                ["netsh", "interface", "ipv4", "show", "interfaces"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return self.TUN_NAME in (r.stdout or "")
        except Exception:
            return False

    def _configure_adapter(self):
        """Wait for wintun to bring the adapter up, then give it a static IP/DNS
        and make it the system's default route - done here instead of via
        tun2socks' -tun-post-up (see the note in start())."""
        proc = self.proc
        deadline = time.time() + 8
        while time.time() < deadline:
            if proc is not None and proc.poll() is not None:
                return  # tun2socks already exited - nothing to configure
            if self._adapter_present():
                break
            time.sleep(0.3)
        else:
            self.on_output("[tun] the %s adapter never appeared - VPN mode can't route traffic. "
                            "Check that wintun.dll matches your CPU (see About).\n" % self.TUN_NAME)
            return
        ok = self._netsh("interface", "ip", "set", "address", "name=%s" % self.TUN_NAME,
                          "source=static", "addr=%s" % self.TUN_IP, "mask=255.255.255.0")
        ok = self._netsh("interface", "ip", "set", "dns", "name=%s" % self.TUN_NAME,
                          "static", self.DNS, "register=none", "validate=no") and ok
        # Route by interface name, not by gateway IP: the "gateway" here is the
        # TUN adapter's own address, and Windows' legacy `route add ... <gateway>`
        # frequently refuses that with "The route addition failed: The parameter
        # is incorrect" because it isn't a distinct, reachable next-hop. Routing
        # straight to the interface avoids that failure mode.
        ok = self._netsh("interface", "ipv4", "add", "route", "0.0.0.0/0",
                          "interface=%s" % self.TUN_NAME, "metric=5", "store=active") and ok
        self.route_added = ok
        self.on_output(
            "[tun] adapter configured - all system traffic is now routed through the tunnel.\n" if ok
            else "[tun] could not fully configure the adapter - see the errors above.\n")

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None:
            kill_tree(p)
        if self.route_added:
            try:
                subprocess.run(["netsh", "interface", "ipv4", "delete", "route", "0.0.0.0/0",
                                 "interface=%s" % self.TUN_NAME],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self.route_added = False


# ------------------------------------------------------------------ the app
IDLE, STARTING, CONNECTED, RECONNECTING, STOPPING, FAILED = range(6)
MAX_RETRIES = 3


# ------------------------------------------------------------------ the app UI
NAV_ITEMS = ("Home", "Settings", "Logs", "About")
# Fallback glyphs, only used when Pillow isn't installed (see NAV_ICON_DRAW /
# aa_photo below for the crisp, anti-aliased versions actually used).
NAV_ICONS = {"Home": "\u2302", "Settings": "\u2699", "Logs": "\U0001F5B9", "About": "\u2139"}

# (id, short label, protocol) - one chip per Aether transport, shown on the
# engine page for one-click switching.
QUICK_LABELS = {"masque3": "MASQUE3", "masque2": "MASQUE2", "wg": "Wireguard", "gool": "gool", "mim": "MIM"}
QUICK_ENGINES = [(eid, QUICK_LABELS[eid], eid) for eid in QUICK_LABELS if eid in ENGINE_LABEL]
QUICK_CHIP_CHARS = max(len(lbl) for _, lbl, _ in QUICK_ENGINES) + 2


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.q = queue.Queue()
        self.core = Core(lambda t: self.q.put(("log", t)))
        self.tun = TunBridge(lambda t: self.q.put(("log", "[tun2socks] " + t)))
        self.state = IDLE
        self.note = ""
        self.session = 0
        self.attempt = 0
        self.was_connected = False
        self.t0 = 0.0
        self.angle = 0
        self.hover = False
        self.busy = False          # core download in progress
        self.connect_after_dl = False

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Clubapp.VPN")
        except Exception:
            pass
        root.title(APP_NAME)
        root.configure(bg=BG)
        try:
            root.iconbitmap(os.path.join(RES_DIR, "icon.ico"))
        except Exception:
            pass
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        root.geometry("%dx%d" % (self.S(900), self.S(600)))
        root.minsize(self.S(800), self.S(560))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.chip_widgets = {}        # Aether transport chips (Settings)
        self.home_chip_widgets = {}   # Aether transport chips (Home)
        self.region_btns = []         # region dropdown buttons, Home + Settings
        self._region_pop = None
        self.shape_chips = []
        self._flag_cache = {}
        self._style()
        self._build()
        self.show("Home")
        self.set_state(IDLE)
        self._refresh_core_warning()
        restore_system_proxy()          # leftover from a crashed session
        atexit.register(restore_system_proxy)
        root.after(100, self._drain)
        root.after(1000, self._tick)
        if self.cfg["autoconnect"] and find_engine_exe(self.cfg):
            root.after(800, self.connect)

    def S(self, v):
        return int(v * self.scale)

    # ----------------------------------------------------------- styling/UI
    def _style(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT,
                     arrowcolor=CYAN, bordercolor=PANEL3, lightcolor=PANEL2, darkcolor=PANEL2, padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)],
               foreground=[("readonly", TEXT), ("disabled", FAINT)],
               selectbackground=[("readonly", PANEL2)], selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        st.configure("TCheckbutton", background=PANEL, foreground=TEXT, focuscolor=PANEL,
                     indicatorbackground=PANEL2, indicatorforeground=TEXT)
        st.map("TCheckbutton", background=[("active", PANEL)],
               indicatorbackground=[("selected", GREEN), ("disabled", PANEL)],
               foreground=[("disabled", FAINT)])
        st.configure("Vertical.TScrollbar", troughcolor=BG, background=PANEL2, arrowcolor=MUTED,
                     bordercolor=BG, lightcolor=PANEL2, darkcolor=PANEL2)

    def button(self, parent, text, cmd, solid=False, danger=False):
        if solid:
            bg = GREEN if not danger else ROSE
            fg = "#062018"
        else:
            bg, fg = PANEL2, TEXT
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=mix(bg, "#ffffff", .18),
                      activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                      font=(FONT, 10, "bold" if solid else "normal"), padx=self.S(14), pady=self.S(8),
                      disabledforeground=FAINT)
        b.bind("<Enter>", lambda e: b.configure(bg=mix(bg, "#ffffff", .1)) if str(b["state"]) != "disabled" else None)
        b.bind("<Leave>", lambda e: b.configure(bg=bg) if str(b["state"]) != "disabled" else None)
        return b

    def card(self, parent, top_pad=0, radius=14, bg=PANEL):
        """A rounded panel: a Canvas paints the rounded background and an
        ordinary Frame (matching bg) sits on top for content, inset by >=
        the corner radius so its own square corners never show."""
        S = self.S
        outer_bg = parent["bg"]
        cv = tk.Canvas(parent, bg=outer_bg, highlightthickness=0)
        cv.pack(fill="x", pady=(S(top_pad), 0))
        inner = tk.Frame(cv, bg=bg, padx=S(16), pady=S(14))
        win = cv.create_window(0, 0, window=inner, anchor="nw")

        def redraw(_e=None):
            w = max(cv.winfo_width(), 10)
            inner.update_idletasks()
            h = max(inner.winfo_reqheight(), 10)
            cv.configure(height=h)
            cv.itemconfigure(win, width=w)
            cv.delete("bg")
            rounded_rect(cv, 0, 0, w, h, S(radius), bg, tags="bg")
            cv.tag_lower("bg")
        inner.bind("<Configure>", redraw)
        cv.bind("<Configure>", redraw)
        return inner

    def scroll_frame(self, parent):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _sync_scrollregion)

        def _sync_width(event):
            canvas.itemconfigure(win, width=event.width)
        canvas.bind("<Configure>", _sync_width)

        def _wheel(event):
            if event.delta:
                canvas.yview_scroll(-1 * int(event.delta / 120), "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")

        def _bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", _wheel)
            canvas.bind_all("<Button-4>", _wheel)
            canvas.bind_all("<Button-5>", _wheel)

        def _unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        return inner

    def _build(self):
        S = self.S
        root_wrap = tk.Frame(self.root, bg=BG)
        root_wrap.pack(fill="both", expand=True)

        # ---------------------------------------------------------- sidebar
        side = tk.Frame(root_wrap, bg=PANEL, width=S(210))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        logo_row = tk.Frame(side, bg=PANEL)
        logo_row.pack(fill="x", padx=S(18), pady=(S(20), S(4)))
        mark = tk.Canvas(logo_row, width=S(28), height=S(28), bg=PANEL, highlightthickness=0)
        mark.pack(side="left")
        rounded_rect(mark, 0, 0, S(28), S(28), S(8), BLUE)
        draw_gradient(mark, S(3), S(3), S(25), S(25), BLUE, GREEN, steps=20)
        tk.Label(logo_row, text=" " + APP_NAME, bg=PANEL, fg=TEXT, font=(FONT, 13, "bold")).pack(side="left")
        tk.Label(side, text="v" + APP_VERSION, bg=PANEL, fg=FAINT, font=(FONT, 8)).pack(anchor="w", padx=S(20))

        tk.Frame(side, bg=LINE, height=1).pack(fill="x", padx=S(16), pady=S(16))

        self.nav_rows = {}
        for name in NAV_ITEMS:
            row = tk.Frame(side, bg=PANEL, cursor="hand2")
            row.pack(fill="x", padx=S(10), pady=S(3))
            bar = tk.Canvas(row, width=S(3), height=S(30), bg=PANEL, highlightthickness=0)
            bar.pack(side="left")
            body = tk.Frame(row, bg=PANEL)
            body.pack(side="left", fill="x", expand=True, padx=S(8), pady=S(6))
            icon = tk.Label(body, bg=PANEL)
            icon.pack(side="left")
            isize = S(16)
            fn = NAV_ICON_DRAW[name]
            photos = {
                "inactive": aa_photo(isize, isize, lambda d, W, H, fn=fn: fn(d, W, H, hex_rgba(MUTED))),
                "active": aa_photo(isize, isize, lambda d, W, H, fn=fn: fn(d, W, H, hex_rgba(TEXT))),
            }
            if photos["inactive"] is not None:
                icon._photos = photos
                icon.configure(image=photos["inactive"])
            else:
                icon.configure(text=NAV_ICONS[name], font=(FONT, 12), fg=MUTED)
            lbl = tk.Label(body, text=" " + name, bg=PANEL, fg=MUTED, font=(FONT, 11))
            lbl.pack(side="left")
            for w in (row, body, icon, lbl):
                w.bind("<Button-1>", lambda e, n=name: self.show(n))
            self.nav_rows[name] = (row, body, bar, icon, lbl)

        tk.Frame(side, bg=PANEL).pack(fill="both", expand=True)  # spacer
        tk.Frame(side, bg=LINE, height=1).pack(fill="x", padx=S(16), pady=(0, S(10)))
        foot = tk.Frame(side, bg=PANEL)
        foot.pack(fill="x", padx=S(16), pady=(0, S(16)))
        self.status_dot = tk.Canvas(foot, width=S(9), height=S(9), bg=PANEL, highlightthickness=0)
        self.status_dot.pack(side="left")
        self.status_dot_text = tk.Label(foot, text=" Not connected", bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.status_dot_text.pack(side="left")

        # ----------------------------------------------------------- content
        content_wrap = tk.Frame(root_wrap, bg=BG)
        content_wrap.pack(side="left", fill="both", expand=True)
        grad = tk.Canvas(content_wrap, height=S(3), bg=BG, highlightthickness=0)
        grad.pack(fill="x")
        grad.bind("<Configure>", lambda e, cv=grad: (cv.delete("all"),
                  draw_gradient(cv, 0, 0, cv.winfo_width() or 1, S(3), BLUE, GREEN)))

        body = tk.Frame(content_wrap, bg=BG)
        body.pack(fill="both", expand=True, padx=S(26), pady=S(20))
        self.pages = {}
        for name in NAV_ITEMS:
            self.pages[name] = tk.Frame(body, bg=BG)
        self._build_home(self.scroll_frame(self.pages["Home"]))
        self._build_settings(self.scroll_frame(self.pages["Settings"]))
        self._build_logs(self.pages["Logs"])
        self._build_about(self.scroll_frame(self.pages["About"]))

    def show(self, name):
        for n, page in self.pages.items():
            page.pack_forget()
            row, body, bar, icon, lbl = self.nav_rows[n]
            active = n == name
            bg = PANEL2 if active else PANEL
            for w in (row, body, icon, lbl, bar):
                w.configure(bg=bg)
            if hasattr(icon, "_photos"):
                icon.configure(image=icon._photos["active" if active else "inactive"])
            else:
                icon.configure(fg=TEXT if active else MUTED)
            lbl.configure(fg=TEXT if active else MUTED, font=(FONT, 11, "bold" if active else "normal"))
            bar.delete("all")
            if active:
                draw_vgradient(bar, 0, 0, self.S(3), self.S(30), BLUE, GREEN)
        self.pages[name].pack(fill="both", expand=True)

    # ----------------------------------------------------------------- Home
    def _build_home(self, p):
        """Classic dashboard: one big connect button, small engine buttons
        (Aether / Psiphon) under it, and the status card. Everything else
        (Tor, transports, bridges, ...) lives in Settings."""
        S = self.S
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="Dashboard", bg=BG, fg=TEXT, font=(FONT, 18, "bold")).pack(side="left")
        self.engine_badge = tk.Label(top, bg=PANEL2, fg=CYAN, font=(FONT, 9, "bold"), padx=S(10), pady=S(4))
        self.engine_badge.pack(side="right")
        self.button(top, "Telegram", lambda: webbrowser.open(TELEGRAM_URL)).pack(side="right", padx=(0, S(10)))

        hero = tk.Frame(p, bg=BG)
        hero.pack(fill="x", pady=(S(14), S(6)))
        self.cv_size = S(180)
        self.cv = tk.Canvas(hero, width=self.cv_size, height=self.cv_size, bg=BG, highlightthickness=0,
                            cursor="hand2")
        self.cv.pack()
        self.cv.bind("<Button-1>", lambda e: self.toggle())
        self.cv.bind("<Enter>", lambda e: self._hover(True))
        self.cv.bind("<Leave>", lambda e: self._hover(False))
        self.lbl_title = tk.Label(hero, bg=BG, fg=TEXT, font=(FONT, 17, "bold"))
        self.lbl_title.pack(pady=(S(8), 0))
        self.lbl_hint = tk.Label(hero, bg=BG, fg=MUTED, font=(FONT, 10), wraplength=S(520), justify="center")
        self.lbl_hint.pack(pady=(S(2), 0))

        # small engine buttons: one click switches the engine
        chips_wrap = tk.Frame(p, bg=BG)
        chips_wrap.pack(pady=(S(14), S(4)))
        self.family_chips = {}
        for fid in HOME_ENGINES:
            lbl = tk.Label(chips_wrap, text=FAMILY_NAME[fid], font=(FONT, 9, "bold"), padx=S(12), pady=S(6),
                           cursor="hand2", width=12, anchor="center")
            lbl.pack(side="left", padx=S(4))
            lbl.bind("<Button-1>", lambda e, f=fid: self.select_family(f))
            self.family_chips[fid] = lbl
        self._refresh_family_chips()

        # per-engine options: Aether -> transport type, Psiphon -> type + region
        self.home_opts = tk.Frame(p, bg=BG)
        self.home_opts.pack(fill="x")
        self._build_home_options()

        card_wrap = tk.Frame(p, bg=BG)
        card_wrap.pack(fill="x", pady=(S(12), 0))
        card = self.card(card_wrap)
        self.val = {}
        rows = (("Mode", "\u26a1"), ("Route", "\U0001F310"), ("Exit IP", "\U0001F4CD"), ("Response", "\U0001F4F6"))
        for i, (key, glyph) in enumerate(rows):
            lbl_row = tk.Frame(card, bg=PANEL)
            lbl_row.grid(row=i, column=0, sticky="w", pady=S(5))
            tk.Label(lbl_row, text=glyph, bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(side="left")
            tk.Label(lbl_row, text=" " + key, bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(side="left")
            v = tk.Label(card, text="-", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"), anchor="e")
            v.grid(row=i, column=1, sticky="e", pady=S(5))
            self.val[key] = v
        card.columnconfigure(1, weight=1)
        self._refresh_mode_row()

        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(S(14), 0))
        self.btn_test = self.button(row, "Test connection", self.run_test)
        self.btn_test.pack(side="left")

        self.warn = tk.Frame(p, bg=BG)
        wcard = self.card(self.warn, radius=10)
        tk.Label(wcard, text="\u26a0  The Aether core is not installed yet.", bg=PANEL, fg=AMBER,
                 font=(FONT, 10)).pack(side="left")
        self.btn_dl_home = self.button(wcard, "Download core", self.start_download)
        self.btn_dl_home.pack(side="right")

    def _refresh_family_chips(self):
        cur = self.cfg.get("family")
        for fid, w in self.family_chips.items():
            sel = fid == cur
            w.configure(bg=GREEN if sel else PANEL2, fg="#062018" if sel else MUTED)

    def select_family(self, fid, from_settings=False):
        """Switch engine (Home button or Settings dropdown)."""
        if fid == self.cfg.get("family"):
            return True
        if self.state in (STARTING, CONNECTED, RECONNECTING):
            if not messagebox.askyesno(APP_NAME, "%s is running.\n\nDisconnect it and switch to %s?"
                                       % (FAMILY_NAME[self.cfg["family"]], FAMILY_NAME[fid])):
                return False
            self.disconnect()
        self._set("family", fid)
        self._build_engine_options()
        self._build_home_options()
        self._refresh_family_chips()
        self._refresh_mode_row()
        self._update_texts()
        if hasattr(self, "cb_family"):
            self.cb_family.set(FAMILY_NAME[fid])
        return True

    # small widget helpers for the option cards
    def _opt_section(self, title):
        tk.Label(self.opts_holder, text=title, bg=BG, fg=MUTED, font=(FONT, 10, "bold")).pack(
            anchor="w", pady=(self.S(10), self.S(4)))
        card = self.card(self.opts_holder)
        card.columnconfigure(1, weight=1)
        return card

    def _opt_row(self, card, r, label, widget):
        S = self.S
        tk.Label(card, text=label, bg=PANEL, fg=MUTED, font=(FONT, 10)).grid(
            row=r, column=0, sticky="w", pady=S(5))
        widget.grid(row=r, column=1, sticky="ew", padx=(S(14), 0), pady=S(5))

    def _opt_pair_combo(self, card, pairs, key, after=None):
        labels = [l for _, l in pairs]
        cur = next((l for v, l in pairs if v == self.cfg.get(key)), labels[0])
        c = ttk.Combobox(card, state="readonly", values=labels, width=30)
        c.set(cur)

        def picked(_e=None):
            self._set(key, next(v for v, l in pairs if l == c.get()))
            if after:
                after()
        c.bind("<<ComboboxSelected>>", picked)
        return c

    def _opt_entry(self, card, key, values=None):
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        var.trace_add("write", lambda *a: self._set(key, var.get()))
        if values:
            w = ttk.Combobox(card, textvariable=var, values=values, width=30)
        else:
            w = self._entry(card, var)
        w._var = var
        return w

    def _build_engine_options(self):
        """(Re)build the option cards for the selected engine."""
        S = self.S
        fam = self.cfg["family"]
        mode = family_mode(self.cfg)
        for w in self.opts_holder.winfo_children():
            w.destroy()
        self.chip_widgets = {}

        if fam == "tor":
            card = self._opt_section("Tor")
            self._opt_row(card, 0, "Mode", self._opt_pair_combo(
                card, TOR_MODES, "tor_mode", after=self._mode_changed))
            tk.Label(card, text=MODE_HELP[("tor", mode)], bg=PANEL, fg=FAINT, font=(FONT, 9),
                     wraplength=S(330), justify="left").grid(row=1, column=0, columnspan=2, sticky="w")
            self._opt_row(card, 2, "Bridges", self._opt_pair_combo(card, TOR_BRIDGES, "tor_bridges"))
            self._opt_row(card, 3, "Relays as bridges", self._opt_entry(card, "tor_relays", TOR_RELAY_CHOICES))
            self._opt_row(card, 4, "Relay ports", self._opt_pair_combo(card, TOR_RELAY_PORTS, "tor_relay_ports"))
            self._opt_row(card, 5, "Your bridge line(s)", self._opt_entry(card, "tor_bridge_line"))
            fr = tk.Frame(card, bg=PANEL)
            lbl = tk.Label(fr, bg=PANEL, fg=TEXT, font=(FONT, 9), anchor="w",
                           text=os.path.basename(self.cfg.get("tor_bridge_file") or "") or "none")
            lbl.pack(side="left", fill="x", expand=True)

            def pick_bridges():
                path = filedialog.askopenfilename(title="Select a bridge file",
                                                  filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
                if path:
                    self._set("tor_bridge_file", path)
                    lbl.configure(text=os.path.basename(path))

            def clear_bridges():
                self._set("tor_bridge_file", "")
                lbl.configure(text="none")
            self.button(fr, "Clear", clear_bridges).pack(side="right", padx=(S(6), 0))
            self.button(fr, "Browse...", pick_bridges).pack(side="right")
            self._opt_row(card, 6, "Bridge file", fr)
        elif fam == "psiphon":
            card = self._opt_section("Psiphon")
            self._opt_row(card, 0, "Mode", self._opt_pair_combo(
                card, PSIPHON_MODES, "psiphon_mode", after=self._mode_changed))
            tk.Label(card, text=MODE_HELP[("psiphon", mode)], bg=PANEL, fg=FAINT, font=(FONT, 9),
                     wraplength=S(330), justify="left").grid(row=1, column=0, columnspan=2, sticky="w")
            self.cb_shape = self._opt_pair_combo(card, PSIPHON_SHAPES, "psiphon_shape")
            self._opt_row(card, 2, "Psiphon type", self.cb_shape)
            self._opt_row(card, 3, "Exit region", self._region_picker(card, PANEL, anchor="w"))

        if uses_tunnel(self.cfg) and not (mode == "reverse"):
            title = "Aether tunnel" if fam == "aether" else "WARP tunnel (Aether)"
            card = self._opt_section(title)
            chips = tk.Frame(card, bg=PANEL)
            chips.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, S(6)))
            for i, (qid, label, *_r) in enumerate(QUICK_ENGINES):
                lbl = tk.Label(chips, text=label, font=(FONT, 9, "bold"), padx=S(6), pady=S(6),
                               cursor="hand2", width=QUICK_CHIP_CHARS - 2, anchor="center")
                lbl.grid(row=i // 3, column=i % 3, padx=S(3), pady=S(3))
                lbl.bind("<Button-1>", lambda e, q=qid: self._apply_quick_engine(q))
                self.chip_widgets[qid] = lbl
            self._refresh_chips()
            self.cb = {}
            r = 1

            def add_combo(key, label, values, current, cb):
                nonlocal r
                c = ttk.Combobox(card, state="readonly", values=values, width=30)
                c.set(current)
                c.bind("<<ComboboxSelected>>", lambda e: cb(c.get()))
                self.cb[key] = c
                self._opt_row(card, r, label, c)
                r += 1
            add_combo("scan", "Scan mode", SCANS, self.cfg["scan"], lambda v: self._set("scan", v))
            add_combo("noize", "Obfuscation (noize)", ALL_NOIZE, self.cfg["noize"],
                      lambda v: self._set("noize", v))
            ip_current = next(lbl for val, lbl in IP_MODES if val == self.cfg["ip"])
            add_combo("ip", "IP version", [l for _, l in IP_MODES], ip_current,
                      lambda v: self._set("ip", next(val for val, lbl in IP_MODES if lbl == v)))
        elif mode == "reverse":
            tk.Label(self.opts_holder, bg=BG, fg=FAINT, font=(FONT, 9), wraplength=S(360), justify="left",
                     text="The tunnel always uses MASQUE over HTTP/2 in this mode, so there are no "
                          "transport or scan options.").pack(anchor="w", pady=(S(8), 0))

    # ------------------------------------- Home options + flag picker
    def _flag_img(self, code):
        if code not in self._flag_cache:
            self._flag_cache[code] = flag_photo(code, self.S(22), self.S(15))
        return self._flag_cache[code]

    def _chip(self, parent, text, cmd, **kw):
        lbl = tk.Label(parent, text=text, font=(FONT, 9, "bold"), padx=self.S(10), pady=self.S(6),
                       cursor="hand2", anchor="center", bg=PANEL2, fg=MUTED, **kw)
        lbl.bind("<Button-1>", lambda e: cmd())
        return lbl

    def _dd_button(self, parent, bg, anchor, chars, get, opener, icon=False):
        """A dropdown-style button (value + arrow). Click it to open its list.
        get() -> (text, image, glyph) is used to keep the shown value in sync."""
        S = self.S
        wrap = tk.Frame(parent, bg=bg)
        btn = tk.Frame(wrap, bg=PANEL2, cursor="hand2", highlightthickness=1,
                       highlightbackground=PANEL3)
        btn.pack(anchor=anchor)
        flag = tk.Label(btn, bg=PANEL2, fg=CYAN, cursor="hand2")
        if icon:
            flag.pack(side="left", padx=(S(10), S(4)), pady=S(6))
        name = tk.Label(btn, bg=PANEL2, fg=TEXT, font=(FONT, 10, "bold"), cursor="hand2",
                        width=chars, anchor="w")
        name.pack(side="left", padx=(0 if icon else S(10), 0), pady=0 if icon else S(8))
        arrow = tk.Label(btn, text="\u25be", bg=PANEL2, fg=CYAN, font=(FONT, 11), cursor="hand2")
        arrow.pack(side="left", padx=(S(4), S(10)))
        for w in (btn, flag, name, arrow):
            w.bind("<Button-1>", lambda e, b=btn: opener(b))
        self.region_btns.append(dict(btn=btn, flag=flag, name=name, get=get))
        self._refresh_psiphon_ui()
        return wrap

    def _region_get(self):
        cur = (self.cfg.get("psiphon_region") or "").upper()
        if not cur:
            return "Auto (best available)", None, "\u2733"
        return "%s (%s)" % (PSIPHON_REGION_NAME.get(cur, cur), cur), self._flag_img(cur), ""

    def _region_picker(self, parent, bg, anchor="center"):
        return self._dd_button(parent, bg, anchor, 24, self._region_get,
                               self._open_region_menu, icon=True)

    def _mode_picker(self, parent, bg, anchor="center"):
        return self._dd_button(
            parent, bg, anchor, 24,
            lambda: (dict(PSIPHON_MODES).get(self.cfg.get("psiphon_mode"), PSIPHON_MODES[0][1]), None, ""),
            self._open_mode_menu)

    def _shape_picker(self, parent, bg, anchor="center"):
        return self._dd_button(
            parent, bg, anchor, 18,
            lambda: (dict(PSIPHON_SHAPES).get(self.cfg.get("psiphon_shape"), PSIPHON_SHAPES[0][1]), None, ""),
            self._open_shape_menu)

    def _open_region_menu(self, btn):
        rows = [("", "Auto (best available)", None, "\u2733")] + [
            (c, "%s (%s)" % (n, c), self._flag_img(c), "") for c, n in PSIPHON_REGIONS]
        self._open_menu(btn, rows, (self.cfg.get("psiphon_region") or "").upper(),
                        lambda v: self._set("psiphon_region", v))

    def _open_mode_menu(self, btn):
        rows = [(v, l, None, "") for v, l in PSIPHON_MODES]

        def pick(v):
            self._set("psiphon_mode", v)
            self._mode_changed()
        self._open_menu(btn, rows, self.cfg.get("psiphon_mode"), pick)

    def _open_shape_menu(self, btn):
        rows = [(v, l, None, "") for v, l in PSIPHON_SHAPES]
        self._open_menu(btn, rows, self.cfg.get("psiphon_shape"),
                        lambda v: self._set("psiphon_shape", v))

    def _close_region_menu(self):
        pop = getattr(self, "_region_pop", None)
        if pop is not None:
            try:
                self.root.unbind_all("<Button-1>")
                pop.destroy()
            except tk.TclError:
                pass
        self._region_pop = None
        self._pop_owner = None

    def _open_menu(self, btn, rows, cur, pick):
        """Popup list under `btn`. rows = [(value, text, image, glyph)]."""
        if getattr(self, "_region_pop", None) is not None:
            same = getattr(self, "_pop_owner", None) is btn
            self._close_region_menu()
            if same:
                return
        S = self.S
        pop = tk.Toplevel(self.root)
        pop.overrideredirect(True)
        pop.configure(bg=PANEL3)
        self._region_pop = pop
        self._pop_owner = btn
        row_h = S(30)
        width = max(btn.winfo_width(), S(220))
        height = min(len(rows), 8) * row_h
        outer = tk.Frame(pop, bg=PANEL3, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, bg=PANEL2, highlightthickness=0, width=width, height=height)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        cv.pack(side="left", fill="both", expand=True)
        if len(rows) * row_h > height:
            vsb.pack(side="right", fill="y")
        inner = tk.Frame(cv, bg=PANEL2)
        cv.create_window(0, 0, window=inner, anchor="nw", width=width)

        def choose(value):
            self._close_region_menu()
            pick(value)

        def make_row(value, text, img, glyph):
            sel = value == cur
            bg = PANEL3 if sel else PANEL2
            row = tk.Frame(inner, bg=bg, cursor="hand2", height=row_h)
            row.pack(fill="x")
            row.pack_propagate(False)
            ws = [row]
            if img or glyph:
                fl = tk.Label(row, image=img or "", text="" if img else glyph, bg=bg, fg=CYAN,
                              width=0 if img else S(3))
                fl.image = img
                fl.pack(side="left", padx=(S(10), S(8)))
                ws.append(fl)
            tx = tk.Label(row, text=text, bg=bg, fg=TEXT, font=(FONT, 10, "bold" if sel else "normal"),
                          anchor="w", padx=0 if (img or glyph) else S(12))
            tx.pack(side="left", fill="x", expand=True)
            ws.append(tx)

            def paint(on):
                c = BLUE if on else bg
                for w in ws:
                    w.configure(bg=c)
            for w in ws:
                w.bind("<Enter>", lambda e: paint(True))
                w.bind("<Leave>", lambda e: paint(False))
                w.bind("<Button-1>", lambda e, v=value: choose(v))
        for value, text, img, glyph in rows:
            make_row(value, text, img, glyph)
        inner.update_idletasks()
        cv.configure(scrollregion=(0, 0, width, len(rows) * row_h))

        def wheel(e):
            cv.yview_scroll(-1 if (e.delta > 0 or getattr(e, "num", 0) == 4) else 1, "units")
            return "break"
        # Bound on the popup itself (not bind_all): the page's own scroll area
        # calls unbind_all("<MouseWheel>") the moment the pointer leaves it.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            pop.bind(seq, wheel)

        pop.update_idletasks()
        x, y = btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + 2
        ph = pop.winfo_reqheight()
        if y + ph > pop.winfo_screenheight() - S(40):
            y = max(0, btn.winfo_rooty() - ph - 2)
        pop.geometry("+%d+%d" % (x, y))
        pop.lift()
        idx = next((i for i, r in enumerate(rows) if r[0] == cur), 0)
        total = len(rows) * row_h
        cv.yview_moveto(max(0.0, min(1.0, (idx * row_h - height / 2) / float(total))))

        def outside(e):
            w = e.widget
            try:
                if str(w).startswith(str(pop)) or str(w).startswith(str(btn)):
                    return
            except Exception:
                pass
            self._close_region_menu()
        self.root.bind_all("<Button-1>", outside, add="+")
        pop.bind("<Escape>", lambda e: self._close_region_menu())
        pop.focus_force()   # Windows sends wheel events to the focused window

    def _build_home_options(self):
        """Home page: only the options of the selected engine - Aether shows its
        transport types, Psiphon shows its type and the exit region (with flags)."""
        S = self.S
        for w in self.home_opts.winfo_children():
            w.destroy()
        self.home_chip_widgets = {}
        self.shape_chips = []
        fam = self.cfg.get("family")

        def head(text):
            tk.Label(self.home_opts, text=text, bg=BG, fg=MUTED, font=(FONT, 9, "bold")).pack(
                pady=(S(8), S(3)))
        if fam == "aether":
            head("Aether type")
            row = tk.Frame(self.home_opts, bg=BG)
            row.pack()
            for qid, label, *_r in QUICK_ENGINES:
                lbl = self._chip(row, label, lambda q=qid: self._apply_quick_engine(q),
                                 width=QUICK_CHIP_CHARS - 2)
                lbl.pack(side="left", padx=S(3))
                self.home_chip_widgets[qid] = lbl
            self._refresh_chips()
        elif fam == "psiphon":
            row = tk.Frame(self.home_opts, bg=BG)
            row.pack(pady=(S(8), 0))
            for i, (cap, maker) in enumerate((("Mode", self._mode_picker),
                                              ("Psiphon type", self._shape_picker))):
                col = tk.Frame(row, bg=BG)
                col.grid(row=0, column=i, padx=S(8))
                tk.Label(col, text=cap, bg=BG, fg=MUTED, font=(FONT, 9, "bold")).pack(pady=(0, S(3)))
                maker(col, BG).pack()
            head("Region")
            self._region_picker(self.home_opts, BG).pack()
            self._refresh_psiphon_ui()

    def _refresh_psiphon_ui(self):
        """Keep every dropdown button (Home + Settings) in sync with the settings."""
        shape = self.cfg.get("psiphon_shape", "auto")
        self.region_btns = [d for d in self.region_btns if d["btn"].winfo_exists()]
        for d in self.region_btns:
            text, img, glyph = d["get"]()
            d["flag"].configure(image=img or "", text="" if img else glyph)
            d["flag"].image = img
            d["name"].configure(text=text)
        cb = getattr(self, "cb_shape", None)
        if cb is not None and cb.winfo_exists():
            cb.set(next((l for v, l in PSIPHON_SHAPES if v == shape), PSIPHON_SHAPES[0][1]))
        if hasattr(self, "val"):
            self._refresh_mode_row()

    def _family_from_combo(self, _e=None):
        name = self.cb_family.get()
        fid = next(f for f in FAMILY_IDS if FAMILY_NAME[f] == name)
        if not self.select_family(fid):
            self.cb_family.set(FAMILY_NAME[self.cfg["family"]])

    def _mode_changed(self):
        self._build_engine_options()
        self._refresh_mode_row()
        self._update_texts()

    def _refresh_chips(self):
        current = self.cfg["protocol"]
        for group in (getattr(self, "chip_widgets", {}), getattr(self, "home_chip_widgets", {})):
            for qid, w in group.items():
                if w.winfo_exists():
                    sel = qid == current
                    w.configure(bg=GREEN if sel else PANEL2, fg="#062018" if sel else MUTED)

    def _apply_quick_engine(self, qid):
        self._set("protocol", qid)
        self._refresh_chips()
        self._refresh_mode_row()
        self._update_texts()

    def _hover(self, on):
        self.hover = on
        self._draw()

    def _refresh_mode_row(self):
        vpn = bool(self.cfg.get("vpn_mode"))
        text = "Full VPN (all system traffic)" if vpn else "System proxy only"
        color = CYAN if vpn else TEXT
        if "Mode" in self.val:
            self.val["Mode"].configure(text=text, fg=color)
        if hasattr(self, "engine_badge"):
            fam = self.cfg["family"]
            if fam == "aether":
                label = "Aether \u00b7 " + ENGINE_LABEL[self.cfg["protocol"]].split(" \u2014 ")[-1].split(" (")[0]
            else:
                label = FAMILY_NAME[fam]
                if fam == "psiphon" and self.cfg.get("psiphon_region"):
                    label += " \u00b7 " + self.cfg["psiphon_region"]
                label += " \u00b7 " + family_mode(self.cfg)
            self.engine_badge.configure(text=label)

    def _refresh_core_warning(self):
        path = find_core(self.cfg)
        if hasattr(self, "lbl_core"):
            self.lbl_core.configure(text=path or "Not found - use Download core or Browse")
        if hasattr(self, "lbl_pt"):
            ps, ly = find_pt_bin(self.cfg, PSIPHON_EXE), find_pt_bin(self.cfg, LYREBIRD_EXE)
            self.lbl_pt.configure(
                text="Psiphon: %s\nTor bridges (lyrebird): %s" % (
                    ps or "not found", ly or "not found"),
                fg=TEXT if (ps and ly) else AMBER)
        if path:
            self.warn.pack_forget()
        else:
            self.warn.pack(fill="x", pady=(self.S(12), 0))

    # ------------------------------------------------------------- Settings
    def _build_settings(self, p):
        S = self.S

        def section(title):
            tk.Label(p, text=title, bg=BG, fg=MUTED, font=(FONT, 10, "bold")).pack(
                anchor="w", pady=(S(14), S(4)))
            card = self.card(p)
            card.columnconfigure(1, weight=1)
            return card

        def add_row(card, r, label, widget):
            tk.Label(card, text=label, bg=PANEL, fg=MUTED, font=(FONT, 10)).grid(
                row=r, column=0, sticky="w", pady=S(5))
            widget.grid(row=r, column=1, sticky="ew", padx=(S(14), 0), pady=S(5))

        def text_row(card, r, label, key):
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            var.trace_add("write", lambda *a: self._set(key, var.get()))
            add_row(card, r, label, self._entry(card, var))
            return var

        def port_row(card, r, label, key):
            var = tk.StringVar(value=str(self.cfg[key]))

            def changed(*a):
                v = var.get().strip()
                if v.isdigit() and 1024 <= int(v) <= 65535:
                    self._set(key, int(v))
            var.trace_add("write", changed)
            add_row(card, r, label, self._entry(card, var))
            return var

        def file_row(card, r, label, key, filetypes):
            fr = tk.Frame(card, bg=PANEL)
            lbl = tk.Label(fr, bg=PANEL, fg=TEXT, font=(FONT, 9), anchor="w",
                           text=os.path.basename(self.cfg.get(key) or "") or "none")
            lbl.pack(side="left", fill="x", expand=True)

            def pick():
                path = filedialog.askopenfilename(title="Select " + label, filetypes=filetypes)
                if path:
                    self._set(key, path)
                    lbl.configure(text=os.path.basename(path))

            def clear():
                self._set(key, "")
                lbl.configure(text="none")
            self.button(fr, "Clear", clear).pack(side="right", padx=(S(6), 0))
            self.button(fr, "Browse...", pick).pack(side="right")
            add_row(card, r, label, fr)

        def pair_combo(card, pairs, key):
            labels = [l for _, l in pairs]
            cur = next((l for v, l in pairs if v == self.cfg.get(key)), labels[0])
            c = ttk.Combobox(card, state="readonly", values=labels, width=26)
            c.set(cur)
            c.bind("<<ComboboxSelected>>", lambda e: self._set(key, next(v for v, l in pairs if l == c.get())))
            return c

        # --- Engine ------------------------------------------------------
        eng0 = section("Engine")
        self.cb_family = ttk.Combobox(eng0, state="readonly", values=[FAMILY_NAME[f] for f in FAMILY_IDS],
                                      width=26)
        self.cb_family.set(FAMILY_NAME[self.cfg["family"]])
        self.cb_family.bind("<<ComboboxSelected>>", self._family_from_combo)
        add_row(eng0, 0, "Connect with", self.cb_family)
        tk.Label(eng0, text="Aether and Psiphon also have buttons on the Home page.", bg=PANEL, fg=FAINT,
                 font=(FONT, 9)).grid(row=1, column=0, columnspan=2, sticky="w")
        self.opts_holder = tk.Frame(p, bg=BG)
        self.opts_holder.pack(fill="x")
        self.chip_widgets = {}
        self._build_engine_options()

        # --- Ports ------------------------------------------------------
        eng = section("Local proxy ports")
        self.v_port = port_row(eng, 0, "Main SOCKS5 port (Aether)", "port")
        self.v_tor_port = port_row(eng, 1, "Tor proxy port", "tor_port")
        self.v_psiphon_port = port_row(eng, 2, "Psiphon proxy port", "psiphon_port")

        # --- Aether tuning ----------------------------------------------
        aeth = section("Aether tuning")
        lc = ttk.Combobox(aeth, state="readonly", values=LOG_LEVELS, width=26)
        lc.set(self.cfg.get("log_level", "info"))
        lc.bind("<<ComboboxSelected>>", lambda e: self._set("log_level", lc.get()))
        add_row(aeth, 0, "Log level", lc)
        text_row(aeth, 1, "Manual endpoint (ip:port)", "peer")
        text_row(aeth, 2, "Exit country filter (e.g. !IR,RU)", "exit_loc")
        text_row(aeth, 3, "DNS inside tunnel (e.g. 1.1.1.1,1.0.0.1)", "dns")
        text_row(aeth, 4, "Extra arguments", "extra")

        # --- Newer Aether options -----------------------------------------
        adv = section("Aether advanced")
        text_row(adv, 0, "HTTP proxy (e.g. 127.0.0.1:10809)", "http_proxy")
        text_row(adv, 1, "Dial out through (socks5://host:port)", "upstream")
        text_row(adv, 2, "WARP-in-WARP hops (outer,inner)", "wiw_peers")
        text_row(adv, 3, "MASQUE-in-MASQUE hops (outer,inner)", "mim_peers")
        text_row(adv, 4, "Encrypted Client Hello (auto / base64)", "ech")
        pc = ttk.Combobox(adv, state="readonly", values=[l for _, l in PERF_PROFILES], width=26)
        pc.set(next(l for v, l in PERF_PROFILES if v == self.cfg.get("perf", "")))
        pc.bind("<<ComboboxSelected>>", lambda e: self._set("perf", next(v for v, l in PERF_PROFILES if l == pc.get())))
        add_row(adv, 5, "Resource profile", pc)
        file_row(adv, 6, "Routing rules file", "routes_file", [("Text files", "*.txt *.toml *.conf"), ("All files", "*.*")])
        text_row(adv, 7, "Block (domains, IPs, ports)", "route_block")
        text_row(adv, 8, "Bypass tunnel (domains, IPs, private)", "route_direct")
        adv_tg = tk.Frame(adv, bg=PANEL)
        adv_tg.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(S(6), 0))
        self.v_frag = tk.BooleanVar(value=self.cfg.get("fragment", False))
        self._toggle_row(adv_tg, "Fragment the TLS ClientHello (MASQUE over HTTP/2)", self.v_frag,
                         lambda: self._set("fragment", self.v_frag.get()))
        self.v_nq2 = tk.BooleanVar(value=self.cfg.get("no_quic_v2", False))
        self._toggle_row(adv_tg, "Don't send the QUIC v2 opener", self.v_nq2,
                         lambda: self._set("no_quic_v2", self.v_nq2.get()))
        self.v_ndc = tk.BooleanVar(value=self.cfg.get("no_data_check", False))
        self._toggle_row(adv_tg, "Skip the end-to-end data check", self.v_ndc,
                         lambda: self._set("no_data_check", self.v_ndc.get()))

        # --- Psiphon advanced ---------------------------------------------
        psi = section("Psiphon advanced")
        text_row(psi, 0, "CDN fronting IPs (comma separated)", "psiphon_cdn_ips")
        text_row(psi, 1, "CDN server names (SNI)", "psiphon_cdn_sni")
        file_row(psi, 2, "Psiphon config (JSON)", "psiphon_config", [("JSON", "*.json"), ("All files", "*.*")])

        # --- Behavior -----------------------------------------------------
        beh = section("Behavior")
        self.v_vpn = tk.BooleanVar(value=self.cfg["vpn_mode"])
        self._toggle_row(beh, "VPN mode - route ALL system traffic through a virtual adapter "
                          "(tun2socks + Wintun, needs admin)", self.v_vpn, self._vpn_toggled)
        self.v_sys = tk.BooleanVar(value=self.cfg["system_proxy"])
        self.chk_sys = self._toggle_row(beh, "Route Windows traffic through the tunnel (system proxy)",
                                        self.v_sys, self._sys_toggled)
        self.v_auto = tk.BooleanVar(value=self.cfg["autoconnect"])
        self._toggle_row(beh, "Connect when the app starts (with the last used engine)", self.v_auto,
                          lambda: self._set("autoconnect", self.v_auto.get()))
        self.v_http2 = tk.BooleanVar(value=self.cfg.get("http2", False))
        self._toggle_row(beh, "Force HTTP/2 transport (Aether MASQUE only)", self.v_http2,
                          lambda: self._set("http2", self.v_http2.get()))
        self.v_stats = tk.BooleanVar(value=self.cfg.get("stats", False))
        self._toggle_row(beh, "Log tunnel stats periodically (Aether only)", self.v_stats,
                          lambda: self._set("stats", self.v_stats.get()))
        self.v_qr = tk.BooleanVar(value=self.cfg.get("quick_reconnect", False))
        self._toggle_row(beh, "Quick reconnect \u2014 reuse the last working gateway (Aether only)",
                          self.v_qr, lambda: self._set("quick_reconnect", self.v_qr.get()))
        self._sync_vpn_checkbox_state()

        # --- Aether core ----------------------------------------------
        core = section("Aether core")
        tk.Label(core, text="Location", bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        self.lbl_core = tk.Label(core, bg=PANEL, fg=TEXT, font=(FONT, 9), wraplength=S(520),
                                 justify="left", anchor="w")
        self.lbl_core.pack(fill="x", pady=(S(2), S(8)))
        tk.Label(core, text="Psiphon / Tor helpers (the \"pt\" folder next to aether.exe)", bg=PANEL, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w")
        self.lbl_pt = tk.Label(core, bg=PANEL, fg=TEXT, font=(FONT, 9), wraplength=S(520),
                               justify="left", anchor="w")
        self.lbl_pt.pack(fill="x", pady=(S(2), S(8)))
        r = tk.Frame(core, bg=PANEL)
        r.pack(fill="x")
        self.btn_dl = self.button(r, "Download / update core", self.start_download, solid=True)
        self.btn_dl.pack(side="left")
        self.button(r, "Browse...", self.browse_core).pack(side="left", padx=S(8))

        # --- Other engine files -----------------------------------------
        paths = section("VPN-mode files (download manually, see About)")

        def path_row(parent, text, key, filetypes):
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", pady=S(3))
            tk.Label(row, text=text, bg=PANEL, fg=TEXT, font=(FONT, 9), width=16, anchor="w").pack(side="left")
            lbl = tk.Label(row, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w",
                          text=os.path.basename(self.cfg.get(key) or "") or "not set")
            lbl.pack(side="left", fill="x", expand=True)
            def pick():
                path = filedialog.askopenfilename(title="Select " + text, filetypes=filetypes)
                if path:
                    self._set(key, path)
                    lbl.configure(text=os.path.basename(path))
            self.button(row, "Browse...", pick).pack(side="right")

        path_row(paths, "tun2socks.exe", "tun2socks_path", [("tun2socks.exe", "*.exe"), ("All files", "*.*")])

        tk.Label(p, text="Changes apply the next time you connect.", bg=BG, fg=FAINT,
                 font=(FONT, 9)).pack(anchor="w", pady=(S(10), S(14)))

        # --- version, at the very end of Settings
        tk.Frame(p, bg=LINE, height=1).pack(fill="x", pady=(0, S(10)))
        tk.Label(p, text="%s  \u00b7  Version %s%s" % (APP_NAME, APP_VERSION, "  \u00b7  Portable" if PORTABLE else ""),
                 bg=BG, fg=MUTED,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, S(6)))
        gh = tk.Label(p, text="GitHub  \u00b7  github.com/Mrmmd2004", bg=BG, fg=BLUE,
                      font=(FONT, 10, "underline"), cursor="hand2")
        gh.pack(anchor="w", pady=(0, S(20)))
        gh.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))

    def _toggle_row(self, parent, text, var, command):
        """A modern rounded on/off switch (canvas pill + knob) paired with a
        label, used instead of plain checkboxes for every boolean setting."""
        S = self.S
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=S(6))
        sw_w, sw_h = S(36), S(20)
        sw = tk.Canvas(row, width=sw_w, height=sw_h, bg=PANEL, highlightthickness=0, cursor="hand2")
        sw.pack(side="left")
        lbl = tk.Label(row, text=" " + text, bg=PANEL, fg=TEXT, font=(FONT, 9),
                       wraplength=S(430), justify="left", anchor="w")
        lbl.pack(side="left", fill="x", expand=True)

        def paint():
            on = var.get()
            track = GREEN if on else PANEL3
            photo = aa_photo(sw_w, sw_h, lambda dr, W, H, on=on, track=track:
                              aa_switch_draw(track, on)(dr, W, H))
            sw.delete("all")
            if photo is not None:
                sw._photo = photo
                sw.create_image(0, 0, anchor="nw", image=photo)
            else:
                rounded_rect(sw, 0, 0, sw_w, sw_h, sw_h // 2, track)
                d = sw_h - S(4)
                x = (sw_w - d - S(2)) if on else S(2)
                sw.create_oval(x, S(2), x + d, S(2) + d, fill="#ffffff", outline="")

        row.locked = False

        def flip(_e=None):
            if row.locked:
                return
            var.set(not var.get())
            paint()
            command()

        sw.bind("<Button-1>", flip)
        lbl.bind("<Button-1>", flip)
        paint()
        row._switch, row._label = sw, lbl
        return row  # callers (e.g. the system-proxy row) can lock/dim this

    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
                        highlightthickness=1, highlightbackground=PANEL3, highlightcolor=BLUE, font=(FONT, 10))

    def _set(self, key, value):
        self.cfg[key] = value
        save_settings(self.cfg)
        if key in ("vpn_mode", "protocol", "tor_mode", "psiphon_mode"):
            self._refresh_mode_row()
        if key in ("psiphon_region", "psiphon_shape", "psiphon_mode"):
            self._refresh_psiphon_ui()

    def _vpn_toggled(self):
        self._set("vpn_mode", self.v_vpn.get())
        self._sync_vpn_checkbox_state()

    def _sync_vpn_checkbox_state(self):
        # The system-proxy switch is meaningless (and dimmed) once full VPN
        # mode is on, since VPN mode already routes everything.
        locked = bool(self.cfg["vpn_mode"])
        self.chk_sys.locked = locked
        self.chk_sys._switch.configure(cursor="arrow" if locked else "hand2")
        self.chk_sys._label.configure(fg=FAINT if locked else TEXT)

    def _sys_toggled(self):
        self._set("system_proxy", self.v_sys.get())
        if self.state == CONNECTED:
            if self.cfg["system_proxy"]:
                enable_system_proxy(active_port(self.cfg))
            else:
                restore_system_proxy()
        self._update_texts()

    def browse_core(self):
        path = filedialog.askopenfilename(title="Select aether.exe", filetypes=[("Aether core", "*.exe"), ("All files", "*.*")])
        if path:
            self._set("core_path", path)
            self._refresh_core_warning()

    # ----------------------------------------------------------------- Logs
    def _build_logs(self, p):
        S = self.S
        tk.Label(p, text="Logs", bg=BG, fg=TEXT, font=(FONT, 18, "bold")).pack(anchor="w", pady=(0, S(10)))
        wrap = tk.Frame(p, bg=PANEL)
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, bg=PANEL, fg=TEXT, relief="flat", wrap="word", font=(FONT_MONO, 9),
                           padx=S(10), pady=S(10), state="disabled", highlightthickness=0, insertbackground=TEXT)
        sb = ttk.Scrollbar(wrap, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("err", foreground=ROSE)
        self.log.tag_configure("warn", foreground=AMBER)
        self.log.tag_configure("ok", foreground=CYAN)
        self.log.tag_configure("app", foreground=GREEN)

        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(S(8), 0))
        self.v_send = tk.StringVar()
        ent = self._entry(row, self.v_send)
        ent.pack(side="left", fill="x", expand=True, ipady=S(4))
        ent.bind("<Return>", lambda e: self._send())
        self.button(row, "Send", self._send).pack(side="left", padx=(S(6), 0))
        self.button(row, "Copy", self._copy_log).pack(side="left", padx=(S(6), 0))
        self.button(row, "Clear", self._clear_log).pack(side="left", padx=(S(6), 0))
        tk.Label(p, text="Send is only needed if the core asks a question.", bg=BG, fg=FAINT,
                 font=(FONT, 9)).pack(anchor="w", pady=(S(4), 0))

    def append_log(self, text, tag=None):
        text = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        low = text.lower()
        if not tag:
            if any(w in low for w in ("error", "fail", "panic", "refused")):
                tag = "err"
            elif "warn" in low:
                tag = "warn"
            elif any(w in low for w in ("listening", "connected", "success", "ready", "established")):
                tag = "ok"
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 3000:
            self.log.delete("1.0", "%d.0" % (lines - 3000))
        self.log.see("end")
        self.log.configure(state="disabled")

    def note_log(self, msg):
        self.append_log(msg + "\n", "app")

    def _send(self):
        text = self.v_send.get()
        self.v_send.set("")
        self.append_log("> %s\n" % text, "app")
        self.core.send(text)

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end-1c"))

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ---------------------------------------------------------------- About
    def _build_about(self, p):
        S = self.S
        tk.Label(p, text="About", bg=BG, fg=TEXT, font=(FONT, 18, "bold")).pack(anchor="w", pady=(0, S(10)))
        card = self.card(p)
        tk.Label(card, text=APP_NAME, bg=PANEL, fg=TEXT, font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(card, text="Version " + APP_VERSION, bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        tk.Label(card, text="A desktop client for the Aether circumvention engine with Psiphon and Tor "
                            "built in, used either as a local SOCKS5 proxy or, in VPN mode, as a "
                            "full-system tunnel. The Android app and news are published on our "
                            "Telegram channel.",
                 bg=PANEL, fg=TEXT, font=(FONT, 10), wraplength=S(560), justify="left").pack(anchor="w", pady=(S(8), S(10)))
        r = tk.Frame(card, bg=PANEL)
        r.pack(fill="x")
        self.button(r, "Telegram channel", lambda: webbrowser.open(TELEGRAM_URL), solid=True).pack(side="left")
        self.button(r, "Android version", lambda: webbrowser.open(ANDROID_URL)).pack(side="left", padx=S(8))

        dl = self.card(p, top_pad=10)
        tk.Label(dl, text="Download links", bg=PANEL, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w", pady=(0, S(6)))

        def link_row(text, url, note=None):
            row = tk.Frame(dl, bg=PANEL)
            row.pack(fill="x", pady=S(3))
            self.button(row, text, lambda u=url: webbrowser.open(u)).pack(side="left")
            if note:
                tk.Label(row, text=note, bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=S(360),
                         justify="left").pack(side="left", padx=(S(8), 0))

        link_row("Aether core", CORE_REPO, "Settings \u2192 Download / update core does this automatically "
                 "and also fetches the pt folder (Psiphon and Tor helpers).")
        link_row("tun2socks", TUN2SOCKS_URL, "Needed for VPN mode. Pick tun2socks.exe in Settings.")
        link_row("Wintun driver", WINTUN_URL, "Copy wintun.dll (matching your CPU arch) next to "
                 "tun2socks.exe. Also needed for VPN mode.")

        credit = self.card(p, top_pad=10)
        tk.Label(credit, text="Powered by Aether by CluvexStudio (AGPL-3.0), Psiphon, Tor / lyrebird, "
                              "xjasonlyu/tun2socks and Wintun. Clubapp VPN itself only starts these "
                              "and manages the connection/routing.",
                 bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=S(560), justify="left").pack(anchor="w")
        self.button(credit, "Aether on GitHub", lambda: webbrowser.open(CORE_REPO)).pack(anchor="w", pady=(S(8), 0))

    # ------------------------------------------------------- connect button
    def _btn_style(self):
        if self.state == CONNECTED:
            return GREEN, mix(GREEN, BG, .85), GREEN
        if self.state in (STARTING, RECONNECTING, STOPPING):
            return AMBER, PANEL2, TEXT
        if self.state == FAILED:
            return ROSE, PANEL2, TEXT
        return BLUE, PANEL2, TEXT

    def _draw(self):
        c, size = self.cv, self.cv_size
        c.delete("all")
        cx = cy = size / 2
        ring_w = self.S(6)
        R = size / 2 - ring_w
        ring, fill, icon = self._btn_style()
        if self.state == CONNECTED or self.hover:
            g = self.S(10)
            c.create_oval(cx - R - g, cy - R - g, cx + R + g, cy + R + g, fill=mix(ring, BG, .85), outline="")
        # Anti-aliased render (Pillow, supersampled + downsampled) so the
        # ring and power glyph are smooth instead of Tk's jagged ovals/arcs.
        photo = None
        if self.state == CONNECTED:
            photo = aa_power_button(size, ring, fill, icon, ring_w, filled_ratio=1.0)
        elif self.state in (STARTING, RECONNECTING, STOPPING):
            photo = aa_power_button(size, ring, fill, icon, ring_w, arc_start=self.angle, arc_extent=80)
        else:
            photo = aa_power_button(size, ring, fill, icon, ring_w)
        if photo is not None:
            self._btn_photo = photo  # keep a reference so it isn't garbage-collected
            c.create_image(0, 0, anchor="nw", image=photo)
            return
        # Pillow not installed - fall back to the original plain Tk drawing.
        if self.state == CONNECTED:
            steps = 48
            for i in range(steps):
                a0 = i * (360.0 / steps)
                color = mix(BLUE, GREEN, i / (steps - 1))
                c.create_arc(cx - R, cy - R, cx + R, cy + R, start=a0, extent=360.0 / steps + 1,
                            style="arc", outline=color, width=ring_w)
        elif self.state in (STARTING, RECONNECTING, STOPPING):
            c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=mix(ring, BG, .6), width=ring_w)
            c.create_arc(cx - R, cy - R, cx + R, cy + R, start=self.angle, extent=80,
                        style="arc", outline=ring, width=ring_w)
        else:
            c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=ring, width=ring_w)
        c.create_oval(cx - R + ring_w, cy - R + ring_w, cx + R - ring_w, cy + R - ring_w, fill=fill, outline="")
        s = int(R * 0.4)
        lw = max(4, self.S(4))
        c.create_arc(cx - s, cy - s + 3, cx + s, cy + s + 3, start=125, extent=290, style="arc", outline=icon, width=lw)
        c.create_line(cx, cy - int(s * 1.15), cx, cy - int(s * 0.1), fill=icon, width=lw, capstyle="round")

    def _animate(self):
        if self.state in (STARTING, RECONNECTING, STOPPING):
            self.angle = (self.angle - 10) % 360
            self._draw()
            self.root.after(40, self._animate)

    # ---------------------------------------------------------------- state
    def set_state(self, state, note=""):
        was_spinning = self.state in (STARTING, RECONNECTING, STOPPING)
        self.state, self.note = state, note
        self._update_texts()
        self._draw()
        if state in (STARTING, RECONNECTING, STOPPING) and not was_spinning:
            self._animate()
        if state != CONNECTED:
            self.btn_test.configure(state="disabled")

    def _update_texts(self):
        port = active_port(self.cfg)
        title, hint, color = {
            IDLE: ("Not connected", "Click the button to connect.", TEXT),
            STARTING: ("Connecting...", "Finding a working route. This can take a minute.", AMBER),
            CONNECTED: ("Connected", "", GREEN),
            RECONNECTING: ("Reconnecting...", "Attempt %d of %d" % (self.attempt, MAX_RETRIES), AMBER),
            STOPPING: ("Disconnecting...", "", AMBER),
            FAILED: ("Connection failed", self.note, ROSE),
        }[self.state]
        if self.state == CONNECTED:
            hint = ("All system traffic is going through the VPN adapter." if self.cfg["vpn_mode"] else
                    "Windows traffic is going through the tunnel." if self.cfg["system_proxy"]
                    else "SOCKS5 proxy ready at 127.0.0.1:%d" % port)
            self.val["Route"].configure(text=route_text(self.cfg))
            self.btn_test.configure(state="normal")
        elif self.state in (IDLE, FAILED):
            self.val["Route"].configure(text="-")
            self.val["Exit IP"].configure(text="-")
            self.val["Response"].configure(text="-")
        self.lbl_title.configure(text=title, fg=color)
        self.lbl_hint.configure(text=hint)
        self.status_dot.delete("all")
        self.status_dot.create_oval(0, 0, self.S(9), self.S(9), fill=color if color != TEXT else FAINT, outline="")
        self.status_dot_text.configure(text=" " + title)

    def _tick(self):
        if self.state == CONNECTED:
            elapsed = fmt_time(time.time() - self.t0)
            if self.cfg["vpn_mode"]:
                text = "%s  |  VPN mode (all traffic)" % elapsed
            elif self.cfg["system_proxy"]:
                text = "%s  |  Windows traffic is tunnelled" % elapsed
            else:
                text = "%s  |  SOCKS5 at 127.0.0.1:%d" % (elapsed, active_port(self.cfg))
            self.lbl_hint.configure(text=text)
        elif self.state == STARTING:
            self.lbl_hint.configure(text="Finding a working route... %ds" % (time.time() - self.t0))
        self.root.after(1000, self._tick)

    # ----------------------------------------------------------- connecting
    def toggle(self):
        if self.busy:
            return
        if self.state in (IDLE, FAILED):
            self.attempt = 0
            self.was_connected = False
            self.connect()
        elif self.state in (STARTING, CONNECTED, RECONNECTING):
            self.disconnect()

    def connect(self, reconnect=False):
        if self.cfg["vpn_mode"] and not is_admin():
            if messagebox.askyesno(APP_NAME, "VPN mode needs administrator rights to create the "
                                   "virtual network adapter and change the routing table.\n\n"
                                   "Restart Clubapp VPN as administrator now?"):
                relaunch_as_admin()
                self.root.destroy()
            return
        exe = find_engine_exe(self.cfg)
        if not exe:
            if messagebox.askyesno(APP_NAME, "The Aether core is not installed yet.\nDownload it now?"):
                self.connect_after_dl = True
                self.start_download()
            return
        if self.cfg["family"] == "psiphon" and not find_pt_bin(self.cfg, PSIPHON_EXE):
            if messagebox.askyesno(APP_NAME, "The Psiphon helper (psiphon-tunnel-core) is missing.\n"
                                   "It comes with the Aether download (the \"pt\" folder).\n\n"
                                   "Download it now?"):
                self.connect_after_dl = True
                self.start_download()
            return
        ports = ports_needed(self.cfg)
        if len(set(ports)) != len(ports):
            self.set_state(FAILED, "The proxy ports must all be different. Change them in Settings.")
            return
        for port in ports:
            if port_open(port):
                self.set_state(FAILED, "Port %d is already in use. Change it in Settings." % port)
                return
        port = active_port(self.cfg)
        if not reconnect:
            self.ds_retries = 0
        self.psi_ready = False
        self.session += 1
        sid = self.session
        try:
            self.core.start(exe, self.cfg, fresh=reconnect and self.was_connected)
        except OSError as e:
            self.set_state(FAILED, "Could not start the core: %s" % e)
            return
        self.t0 = time.time()
        self.set_state(RECONNECTING if reconnect else STARTING)
        threading.Thread(target=self._watch, args=(sid, self.core.proc, port), daemon=True).start()

    def _watch(self, sid, proc, port):
        deadline = time.time() + start_timeout(self.cfg)
        up = False
        while self.session == sid:
            if proc.poll() is not None:
                self.q.put(("exited", sid, proc.returncode))
                return
            if not up:
                if port_open(port):
                    up = True
                    self.q.put(("up", sid))
                elif time.time() > deadline:
                    self.q.put(("timeout", sid))
                    return
            time.sleep(0.5)

    def disconnect(self):
        self.session += 1
        self.connect_after_dl = False
        self.was_connected = False
        self.tun.stop()
        restore_system_proxy()
        self.set_state(STOPPING)

        def work():
            self.core.stop()
            self.q.put(("stopped",))
        threading.Thread(target=work, daemon=True).start()

    def _retry(self, sid):
        if sid == self.session and self.state == RECONNECTING:
            self.core.stop()
            self.connect(reconnect=True)

    def run_test(self):
        if self.state != CONNECTED:
            return
        self.val["Exit IP"].configure(text="testing...")
        self.val["Response"].configure(text="-")
        self._check_ip(self.session)

    def _check_ip(self, sid):
        port = active_port(self.cfg)

        def work():
            try:
                info = socks5_trace(port)
            except Exception as e:  # noqa: BLE001 - shown to the user
                info = {"error": str(e)}
            self.q.put(("ip", sid, info))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------ core download
    def start_download(self):
        if self.busy:
            return
        if self.core.alive:
            messagebox.showinfo(APP_NAME, "Disconnect first, then update the core.")
            return
        self.busy = True
        self.btn_dl.configure(state="disabled", text="Downloading...")
        self.btn_dl_home.configure(state="disabled", text="Downloading...")

        def work():
            try:
                path = download_core(lambda m: self.q.put(("note", m)))
                self.q.put(("dl_ok", path))
            except Exception as e:  # noqa: BLE001
                self.q.put(("dl_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------- event pump
    def _drain(self):
        try:
            while True:
                try:
                    item = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(item)
                except Exception as e:  # noqa: BLE001 - never kill the pump
                    self.note_log("internal error: %r" % (e,))
        finally:
            self.root.after(100, self._drain)

    def _handle(self, item):
        kind = item[0]
        if kind == "log":
            self.append_log(item[1])
            if (self.cfg.get("family") == "psiphon" and not getattr(self, "psi_ready", False)
                    and "psiphon is ready" in item[1].lower()):
                self.psi_ready = True
                if self.state == CONNECTED:
                    sid = self.session
                    self.root.after(1500, lambda: self._check_ip(sid) if self.session == sid else None)
        elif kind == "note":
            self.note_log(item[1])
        elif kind == "up" and item[1] == self.session:
            self.was_connected = True
            self.attempt = 0
            self.t0 = time.time()
            self.note_log("%s is up on 127.0.0.1:%d" % (FAMILY_NAME[self.cfg["family"]], active_port(self.cfg)))
            if self.cfg["vpn_mode"]:
                tun_exe = find_tun2socks(self.cfg)
                if not tun_exe:
                    self.note_log("tun2socks.exe not found - falling back to system proxy. "
                                  "See the About tab for the download link.")
                    try:
                        enable_system_proxy(active_port(self.cfg))
                    except OSError as e:
                        self.note_log("Could not set the Windows proxy: %s" % e)
                else:
                    try:
                        self.tun.start(tun_exe, active_port(self.cfg))
                    except OSError as e:
                        self.note_log("Could not start VPN mode: %s" % e)
            elif self.cfg["system_proxy"]:
                try:
                    enable_system_proxy(active_port(self.cfg))
                except OSError as e:
                    self.note_log("Could not set the Windows proxy: %s" % e)
            self.set_state(CONNECTED)
            sid = self.session
            if self.cfg.get("family") == "psiphon" and not getattr(self, "psi_ready", False):
                # the proxy port opens before Psiphon has built its tunnel; testing now only
                # produces a false "SOCKS5 connect failed". Wait for "psiphon is ready"
                # (fallback: test anyway after 60 s).
                self.val["Exit IP"].configure(text="waiting for Psiphon...")
                self.val["Response"].configure(text="-")

                def _fallback():
                    if self.session == sid and not getattr(self, "psi_ready", False):
                        self.psi_ready = True
                        self._check_ip(sid)
                self.root.after(60000, _fallback)
            else:
                self.root.after(2000, lambda: self._check_ip(sid) if self.session == sid else None)
        elif kind == "exited" and item[1] == self.session:
            self.tun.stop()
            restore_system_proxy()
            code = item[2]
            self.note_log("Core exited (code %s)." % code)
            if (self.cfg.get("family") == "psiphon" and self.core.ds_fail and not self.was_connected
                    and getattr(self, "ds_retries", 0) < 3):
                # Psiphon could not open its datastore (locked by a leftover process or broken
                # file): kill leftovers and start again with a brand-new data folder.
                self.ds_retries = getattr(self, "ds_retries", 0) + 1
                self.core.psi_slot += 1
                self.attempt = self.ds_retries
                self.note_log("Psiphon datastore problem - retrying with a fresh data folder (%d/3)."
                              % self.ds_retries)
                self.set_state(RECONNECTING)
                sid = self.session
                self.root.after(1500, lambda: self._retry(sid))
            elif self.was_connected and self.attempt < MAX_RETRIES:
                self.attempt += 1
                self.set_state(RECONNECTING)
                sid = self.session
                self.root.after(2000 * self.attempt, lambda: self._retry(sid))
            else:
                self.session += 1
                if self.was_connected:
                    msg = "Lost the connection after %d attempts." % MAX_RETRIES
                else:
                    msg = "The core stopped (code %s). Check the Logs tab." % code
                self.was_connected = False
                self.set_state(FAILED, msg)
        elif kind == "timeout" and item[1] == self.session:
            self.session += 1
            self.core.stop()
            self.set_state(FAILED, "No route found after %d minutes. Try another protocol, "
                           "mode or scan mode." % (start_timeout(self.cfg) // 60))
        elif kind == "stopped":
            if self.state == STOPPING:
                self.set_state(IDLE)
        elif kind == "ip" and item[1] == self.session and self.state == CONNECTED:
            info = item[2]
            if "error" in info:
                self.val["Exit IP"].configure(text="check failed")
                self.note_log("Connection test failed: %s" % info["error"])
            else:
                loc = info.get("loc", "")
                self.val["Exit IP"].configure(text="%s  %s" % (info["ip"], loc))
                self.val["Response"].configure(text="%d ms" % info["ms"])
        elif kind == "dl_ok":
            self.busy = False
            self.btn_dl.configure(state="normal", text="Download / update core")
            self.btn_dl_home.configure(state="normal", text="Download core")
            self._set("core_path", item[1])
            self._refresh_core_warning()
            self.note_log("Aether core ready: %s" % item[1])
            if self.connect_after_dl:
                self.connect_after_dl = False
                self.connect()
        elif kind == "dl_err":
            self.busy = False
            self.connect_after_dl = False
            self.btn_dl.configure(state="normal", text="Download / update core")
            self.btn_dl_home.configure(state="normal", text="Download core")
            self.note_log("Download failed: %s" % item[1])
            messagebox.showerror(APP_NAME, "Could not download the core.\n\n%s\n\n"
                                 "You can download it from %s/releases and select it with Browse." % (item[1], CORE_REPO))

    def on_close(self):
        self.session += 1
        self.tun.stop()
        restore_system_proxy()
        self.core.stop()
        self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
