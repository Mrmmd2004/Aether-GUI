#!/usr/bin/env python3
"""Clubapp VPN - one-shot automated build.

Run this once from inside the project folder on a Windows machine
(double-click it, or `python build_all.py`). In order, it will:

  1. Install PyInstaller and Pillow if they're missing (pip).
  2. Download aether.exe (plus its pt/ folder with the Psiphon and Tor helpers)
     and tun2socks-windows-amd64.exe automatically,
     if they aren't already sitting next to this script.
  3. Freeze clubapp_vpn.py into clubapp_vpn.exe with PyInstaller.
  4. Install Inno Setup 6 automatically if it isn't already installed.
  5. Run build_installer.py, which writes ClubappVPN.iss and compiles
     Output\\ClubappVPN-Setup.exe.

At the end it prints the ONE file you send to other people:

    Output\\ClubappVPN-Setup.exe

That installer already contains the app, the Aether core, and tun2socks -
people just run it (it asks for admin rights) and it installs, adds the
firewall rules, and launches the app. They need nothing else: no Python,
no GitHub, no loose files.

NOT automated: wintun.dll. It's a single file that rarely changes and has
no reliable download API, so this script stops and asks for it once if
it's missing. Get the DLL matching your CPU (amd64 for most PCs) from
https://www.wintun.net/ and drop it next to this script, then run again.
"""
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import winreg
import zipfile

SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print("[build] %s" % msg)


def run(cmd, **kw):
    log("running: %s" % " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=SRC_DIR, **kw)


def ensure_packages():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        log("installing pyinstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    try:
        import PIL  # noqa: F401
    except ImportError:
        log("installing pillow...")
        run([sys.executable, "-m", "pip", "install", "pillow"], check=True)


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "ClubappVPN-build"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pick_windows_asset(assets):
    """Same logic the app itself uses to pick its own core download."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        def fits(n):
            return "arm64" in n or "aarch64" in n
    elif machine in ("amd64", "x86_64", "x64"):
        def fits(n):
            return any(w in n for w in ("x86_64", "x86-64", "amd64", "x64"))
    else:
        def fits(n):
            return any(w in n for w in ("i686", "i386", "win32"))
    cands = [a for a in assets if "windows" in a["name"].lower()
             and a["name"].lower().endswith((".zip", ".exe"))]
    for a in cands:
        if fits(a["name"].lower()):
            return a
    return cands[0] if len(cands) == 1 else None


def ensure_aether():
    dest = os.path.join(SRC_DIR, "aether.exe")
    pt_dir = os.path.join(SRC_DIR, "pt")
    have_pt = all(os.path.isfile(os.path.join(pt_dir, n))
                  for n in ("psiphon-tunnel-core.exe", "lyrebird.exe"))
    if os.path.isfile(dest) and have_pt:
        log("aether.exe and pt/ (Psiphon + Tor helpers) already present, skipping download.")
        return
    log("fetching latest Aether release...")
    try:
        rel = json.loads(http_get("https://api.github.com/repos/CluvexStudio/Aether/releases/latest"))
    except Exception as e:
        log("WARNING: couldn't reach GitHub (%s). Download the Aether zip manually from "
            "https://github.com/CluvexStudio/Aether/releases and put aether.exe and the pt "
            "folder here." % e)
        return
    asset = pick_windows_asset(rel.get("assets", []))
    if not asset:
        log("WARNING: no matching Windows build found. Download the Aether zip manually from "
            "https://github.com/CluvexStudio/Aether/releases and put aether.exe and the pt "
            "folder here.")
        return
    log("downloading %s ..." % asset["name"])
    blob = http_get(asset["browser_download_url"], timeout=180)
    if asset["name"].lower().endswith(".exe"):
        with open(dest, "wb") as f:
            f.write(blob)
        log("WARNING: this release is a bare exe - the pt folder (psiphon-tunnel-core.exe, "
            "lyrebird.exe) is needed for Psiphon and Tor; copy it from the Aether zip.")
    else:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for n in z.namelist():
                base = os.path.basename(n)
                if not base:
                    continue
                parts = n.replace("\\", "/").split("/")
                if base.lower() == "aether.exe":
                    target = dest
                elif "pt" in parts[:-1] and base.lower().endswith(".exe"):
                    os.makedirs(pt_dir, exist_ok=True)
                    target = os.path.join(pt_dir, base)
                else:
                    continue
                with open(target, "wb") as f:
                    f.write(z.read(n))
    log("aether.exe ready." if os.path.isfile(dest) else "aether.exe NOT found in the release - get it manually.")
    log("pt/ helpers ready." if os.path.isdir(pt_dir) else "pt/ folder NOT found in the release - Psiphon/Tor need it.")


def ensure_tun2socks():
    dest = os.path.join(SRC_DIR, "tun2socks-windows-amd64.exe")
    if os.path.isfile(dest):
        log("tun2socks already present, skipping download.")
        return
    log("fetching latest tun2socks release...")
    try:
        rel = json.loads(http_get("https://api.github.com/repos/xjasonlyu/tun2socks/releases/latest"))
    except Exception as e:
        log("WARNING: couldn't reach GitHub (%s). Download tun2socks manually from "
            "https://github.com/xjasonlyu/tun2socks/releases and place it here as "
            "tun2socks-windows-amd64.exe." % e)
        return
    asset = next((a for a in rel.get("assets", [])
                  if "windows" in a["name"].lower() and "amd64" in a["name"].lower()), None)
    if not asset:
        log("WARNING: no matching asset found. Download tun2socks manually.")
        return
    log("downloading %s ..." % asset["name"])
    blob = http_get(asset["browser_download_url"], timeout=180)
    if asset["name"].lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            exe_names = [n for n in z.namelist() if n.lower().endswith(".exe")]
            if exe_names:
                with open(dest, "wb") as f:
                    f.write(z.read(exe_names[0]))
    else:
        with open(dest, "wb") as f:
            f.write(blob)
    log("tun2socks ready." if os.path.isfile(dest) else "tun2socks NOT found in the release - get it manually.")


def check_wintun():
    if os.path.isfile(os.path.join(SRC_DIR, "wintun.dll")):
        return True
    log("wintun.dll is missing and can't be fetched automatically (no stable download API).")
    log("Get the DLL matching your CPU (amd64 for most PCs) from https://www.wintun.net/")
    log("and place it next to this script as wintun.dll, then run this again.")
    return False


def freeze_app():
    run([sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole",
         "--icon", "icon.ico", "--uac-admin", "--name", "clubapp_vpn",
         "clubapp_vpn.py"], check=True)
    built = os.path.join(SRC_DIR, "dist", "clubapp_vpn.exe")
    exe = os.path.join(SRC_DIR, "clubapp_vpn.exe")
    if os.path.isfile(built):
        shutil.copy2(built, exe)
    if not os.path.isfile(exe):
        log("ERROR: PyInstaller did not produce clubapp_vpn.exe.")
        sys.exit(1)
    log("built %s" % exe)


def find_iscc():
    for hive, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Inno Setup 6_is1"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Inno Setup 6_is1"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Inno Setup 6_is1"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                install_loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                cand = os.path.join(install_loc, "ISCC.exe")
                if os.path.isfile(cand):
                    return cand
        except OSError:
            pass
    for cand in (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                 r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ISCC.exe") or shutil.which("ISCC")


def ensure_inno_setup():
    if find_iscc():
        log("Inno Setup already installed.")
        return
    log("Inno Setup not found - downloading the official installer from jrsoftware.org...")
    installer = os.path.join(SRC_DIR, "innosetup-installer.exe")
    blob = http_get("https://jrsoftware.org/download.php/is.exe", timeout=180)
    with open(installer, "wb") as f:
        f.write(blob)
    log("installing Inno Setup silently (Windows may prompt for admin rights)...")
    run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True)
    os.remove(installer)
    if not find_iscc():
        log("ERROR: Inno Setup install finished but ISCC.exe still wasn't found. Install it "
            "manually from https://jrsoftware.org/isinfo.php and re-run this script.")
        sys.exit(1)
    log("Inno Setup installed.")


def main():
    if os.name != "nt":
        print("This build script needs Windows (it drives PyInstaller + Inno Setup for a Windows build).")
        sys.exit(1)
    ensure_packages()
    ensure_aether()
    ensure_tun2socks()
    if not check_wintun():
        sys.exit(1)
    freeze_app()
    ensure_inno_setup()
    run([sys.executable, "build_installer.py"], check=True)
    setup_exe = os.path.join(SRC_DIR, "Output", "ClubappVPN-Setup.exe")
    if os.path.isfile(setup_exe):
        log("=" * 60)
        log("ALL DONE. Send this ONE file to other people:")
        log(setup_exe)
        log("=" * 60)
    else:
        log("Build finished but Output\\ClubappVPN-Setup.exe wasn't found - check the log above.")


if __name__ == "__main__":
    main()
