#!/usr/bin/env python3
"""Clubapp VPN - portable build (no installer).

Run it on a Windows machine from inside the project folder (or just
double-click build_portable.bat). It will:

  1. Install PyInstaller / Pillow if missing, fetch aether.exe + pt\\ and
     tun2socks if they aren't next to this script (same steps as build_all.py).
  2. Freeze clubapp_vpn.py into clubapp_vpn.exe.
  3. Copy everything the app needs into  Output\\ClubappVPN-Portable\\  and zip it
     as  Output\\ClubappVPN-Portable-v<version>.zip

The portable folder contains a "portable.txt" marker: with it, the app keeps its
settings in a "data" folder right next to clubapp_vpn.exe instead of in
%APPDATA%, so the folder can be moved or run from a USB stick. Nothing is
installed and nothing is written outside that folder (VPN mode still needs
administrator rights; the exe asks for them).
"""
import os
import re
import shutil
import sys
import zipfile

import build_all as B

SRC_DIR = B.SRC_DIR
OUT_DIR = os.path.join(SRC_DIR, "Output")
FOLDER = "ClubappVPN-Portable"

FILES = ["clubapp_vpn.exe", "aether.exe", "tun2socks-windows-amd64.exe", "wintun.dll", "icon.ico"]
README = """Clubapp VPN - portable
======================

Run clubapp_vpn.exe (it asks for administrator rights - needed for VPN mode).

* Nothing is installed. Settings are kept in the "data" folder next to
  clubapp_vpn.exe, so you can move or copy this whole folder (or run it from a USB stick).
* Keep these next to clubapp_vpn.exe: aether.exe, the "pt" folder,
  tun2socks-windows-amd64.exe and wintun.dll.
* Delete portable.txt if you'd rather store settings in %APPDATA%\\ClubappVPN.
* Don't put the folder somewhere that needs admin rights to write to
  (e.g. C:\\Program Files) - the app would then fall back to %APPDATA%.
"""


def app_version():
    try:
        with open(os.path.join(SRC_DIR, "clubapp_vpn.py"), encoding="utf-8") as f:
            m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
        return m.group(1) if m else "x"
    except OSError:
        return "x"


def assemble():
    dest = os.path.join(OUT_DIR, FOLDER)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in FILES:
        src = os.path.join(SRC_DIR, name)
        if os.path.isfile(src):
            shutil.copy2(src, dest)
        else:
            B.log("WARNING: %s not found - the portable folder will be missing it." % name)
    pt = os.path.join(SRC_DIR, "pt")
    if os.path.isdir(pt):
        shutil.copytree(pt, os.path.join(dest, "pt"))
    else:
        B.log("WARNING: pt folder not found - Psiphon and Tor need it.")
    with open(os.path.join(dest, "portable.txt"), "w", encoding="utf-8") as f:
        f.write("Marker file: while this file exists next to clubapp_vpn.exe, settings are kept in "
                "the 'data' folder here.\n")
    with open(os.path.join(dest, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(README)
    zpath = os.path.join(OUT_DIR, "ClubappVPN-Portable-v%s.zip" % app_version())
    if os.path.isfile(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder, _, files in os.walk(dest):
            for fn in files:
                full = os.path.join(folder, fn)
                z.write(full, os.path.join(FOLDER, os.path.relpath(full, dest)))
    return dest, zpath


def main():
    if os.name != "nt":
        print("This build script needs Windows (PyInstaller builds for the OS it runs on).")
        sys.exit(1)
    B.ensure_packages()
    B.ensure_aether()
    B.ensure_tun2socks()
    if not B.check_wintun():
        sys.exit(1)
    B.freeze_app()
    dest, zpath = assemble()
    B.log("=" * 60)
    B.log("ALL DONE. Portable folder: %s" % dest)
    B.log("Zip to share:             %s" % zpath)
    B.log("=" * 60)


if __name__ == "__main__":
    main()
