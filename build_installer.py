#!/usr/bin/env python3
"""Clubapp VPN - build the Windows installer with Inno Setup.

Double-click this (or run `python build_installer.py`) from inside the app
folder. It does NOT install anything on your machine - it writes an Inno
Setup script (ClubappVPN.iss) next to itself and runs your existing Inno
Setup compiler (ISCC.exe) on it, producing Setup.exe in an Output\\ folder.

Requirements:
  - Inno Setup 6 (64-bit build support) installed on THIS machine, the one
    you use to build releases. End users never need Python or Inno Setup -
    they just run the Setup.exe this script produces.
  - Ideally, clubapp_vpn.py already frozen into clubapp_vpn.exe (e.g. with
    `pip install Pillow` then
    `pyinstaller --onefile --noconsole --icon icon.ico --uac-admin
    --name clubapp_vpn clubapp_vpn.py`), sitting in this same folder, since
    end users' machines won't have Python either. If no .exe is found this
    script still builds an installer around the .py file and warns you, but
    that installer will only work on machines that have Python installed.
    (Pillow is optional - the app runs without it - but it's what makes the
    sidebar icons, the settings switches and the connect button render
    smoothly instead of pixelated; install it before freezing for the
    sharper UI.)
"""
import os
import subprocess
import sys
import winreg

APP_NAME = "Clubapp VPN"
APP_VERSION = "2.8.8"
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SRC_DIR, "Output")
ISS_PATH = os.path.join(SRC_DIR, "ClubappVPN.iss")

# Everything below is optional - the script only bundles what actually
# exists in SRC_DIR, so it's safe to reuse this on a folder that only has
# some of these.
OPTIONAL_ITEMS = [
    "icon.ico", "wintun.dll", "tun2socks-windows-amd64.exe",
    "run-aether.bat", "aether.exe", "pt", "wintun",
]


def log(msg):
    print("[build] %s" % msg)


def find_entry_point():
    """Prefer a frozen clubapp_vpn.exe (what end users need); fall back to
    the .py source with a warning."""
    exe = os.path.join(SRC_DIR, "clubapp_vpn.exe")
    if os.path.isfile(exe):
        return "clubapp_vpn.exe", True
    py = os.path.join(SRC_DIR, "clubapp_vpn.py")
    if os.path.isfile(py):
        log("WARNING: clubapp_vpn.exe not found - packaging clubapp_vpn.py instead.")
        log("         End users will need Python installed for this build to run.")
        log("         Freeze it first with PyInstaller for a real standalone installer:")
        log("         pyinstaller --onefile --noconsole --icon icon.ico --uac-admin "
            "--name clubapp_vpn clubapp_vpn.py")
        return "clubapp_vpn.py", False
    log("ERROR: neither clubapp_vpn.exe nor clubapp_vpn.py was found next to this script.")
    sys.exit(1)


def find_iscc():
    """Locate Inno Setup's compiler: registry first (most reliable), then
    the usual install paths, then PATH, then ask the user."""
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
    for cand in (
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ):
        if os.path.isfile(cand):
            return cand
    from shutil import which
    found = which("ISCC.exe") or which("ISCC")
    if found:
        return found
    log("Could not find ISCC.exe (Inno Setup's compiler) automatically.")
    typed = input("Paste the full path to ISCC.exe (or press Enter to abort): ").strip('" ')
    if typed and os.path.isfile(typed):
        return typed
    log("Aborting - install Inno Setup 6 from https://jrsoftware.org/isinfo.php and re-run.")
    sys.exit(1)


def write_iss(entry_name, entry_is_exe):
    files_lines = []
    for name in OPTIONAL_ITEMS:
        path = os.path.join(SRC_DIR, name)
        if not os.path.exists(path):
            continue
        if os.path.isdir(path):
            files_lines.append(
                'Source: "%s\\*"; DestDir: "{app}\\%s"; Flags: ignoreversion recursesubdirs createallsubdirs'
                % (path, name))
        else:
            files_lines.append('Source: "%s"; DestDir: "{app}"; Flags: ignoreversion' % path)
    files_lines.append('Source: "%s"; DestDir: "{app}"; Flags: ignoreversion'
                        % os.path.join(SRC_DIR, entry_name))
    files_block = "\n".join(files_lines)

    if entry_is_exe:
        exe_run = "{app}\\clubapp_vpn.exe"
    else:
        # No frozen exe: launch through pythonw so no console window pops up.
        exe_run = "{code:GetPythonw}"

    icon_line = ('SetupIconFile="%s"' % os.path.join(SRC_DIR, "icon.ico")
                 if os.path.isfile(os.path.join(SRC_DIR, "icon.ico")) else "")

    code_section = ""
    if not entry_is_exe:
        code_section = """
[Code]
function GetPythonw(Param: string): string;
begin
  Result := ExpandConstant('{app}\\clubapp_vpn.py');
end;
"""

    lines = [
        '; Auto-generated by build_installer.py - safe to edit and re-run.',
        '#define MyAppName "%s"' % APP_NAME,
        '#define MyAppVersion "%s"' % APP_VERSION,
        '',
        '[Setup]',
        'AppId={{B6F0B6D2-CLUBAPP-VPN-0001}',
        'AppName={#MyAppName}',
        'AppVersion={#MyAppVersion}',
        'DefaultDirName={autopf}\\{#MyAppName}',
        'DefaultGroupName={#MyAppName}',
        'ArchitecturesInstallIn64BitMode=x64',
        'ArchitecturesAllowed=x64',
        'PrivilegesRequired=admin',
        'OutputDir=Output',
        'OutputBaseFilename=ClubappVPN-Setup',
        'Compression=lzma2',
        'SolidCompression=yes',
    ]
    if icon_line:
        lines.append(icon_line)
    lines += [
        'UninstallDisplayIcon={app}\\icon.ico',
        '',
        '[Languages]',
        'Name: "english"; MessagesFile: "compiler:Default.isl"',
        '',
        '[Files]',
        files_block,
        '',
        '[Icons]',
        'Name: "{group}\\%s"; Filename: "%s"; WorkingDir: "{app}"' % (APP_NAME, exe_run),
        'Name: "{autodesktop}\\%s"; Filename: "%s"; WorkingDir: "{app}"' % (APP_NAME, exe_run),
        '',
        '[Run]',
        'Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""%s (in)"" dir=in action=allow program=""%s"" enable=yes"; Flags: runhidden; StatusMsg: "Adding firewall rule..."' % (APP_NAME, exe_run),
        'Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""%s (out)"" dir=out action=allow program=""%s"" enable=yes"; Flags: runhidden; StatusMsg: "Adding firewall rule..."' % (APP_NAME, exe_run),
        'Filename: "powershell"; Parameters: "-NoProfile -Command ""Add-MpPreference -ExclusionPath %s{app}%s""" ' % (chr(39), chr(39)) +
        '; Flags: runhidden; StatusMsg: "Excluding install folder from Defender..."',
        # "shellexec": clubapp_vpn.exe's manifest requires elevation
        # (--uac-admin). Without this flag Inno launches it with a raw
        # CreateProcess call, which cannot elevate and fails with
        # "CreateProcess failed; code 740, The requested operation
        # requires elevation." shellexec routes it through ShellExecute
        # instead, which can show/reuse the elevation prompt correctly.
        'Filename: "%s"; Description: "Launch %s"; Flags: nowait postinstall skipifsilent shellexec' % (exe_run, APP_NAME),
        '',
        '[UninstallRun]',
        'Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""%s (in)"""; Flags: runhidden' % APP_NAME,
        'Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""%s (out)"""; Flags: runhidden' % APP_NAME,
    ]
    if code_section:
        lines += ['', code_section]
    iss = "\n".join(lines)
    with open(ISS_PATH, "w", encoding="utf-8") as f:
        f.write(iss)
    log("wrote %s" % ISS_PATH)


def run_iscc(iscc_path):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log("compiling with %s ..." % iscc_path)
    result = subprocess.run([iscc_path, ISS_PATH], cwd=SRC_DIR)
    if result.returncode != 0:
        log("Inno Setup reported an error (exit code %d). See the output above." % result.returncode)
        sys.exit(result.returncode)
    setup_exe = os.path.join(OUTPUT_DIR, "ClubappVPN-Setup.exe")
    if os.path.isfile(setup_exe):
        log("done: %s" % setup_exe)
        try:
            subprocess.Popen(["explorer", "/select,", setup_exe])
        except Exception:
            pass
    else:
        log("Compile finished but the expected output file was not found - check the Output folder.")


def main():
    if os.name != "nt":
        print("This build script needs Windows (it drives Inno Setup).")
        return
    entry_name, entry_is_exe = find_entry_point()
    iscc = find_iscc()
    write_iss(entry_name, entry_is_exe)
    run_iscc(iscc)


if __name__ == "__main__":
    main()
