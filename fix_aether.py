#!/usr/bin/env python3
"""
fix_aether.py - one-shot cleanup for the Aether / Psiphon
'tryDatastoreOpenDB ... timeout' problem (Windows).

What it does
  1. Kills leftover aether.exe / psiphon-tunnel-core.exe / lyrebird.exe /
     tun2socks processes (the orphans that keep the Psiphon datastore locked).
  2. Shows which process is holding the proxy ports (8888, 10888, 10808, 10820, 10821)
     and frees them if they belong to the programs above.
  3. Optional --clean: deletes the Psiphon datastore files (psiphon.boltdb*)
     so Psiphon starts from a fresh database.

Usage (from CMD):
    python fix_aether.py              # kill orphans + free ports
    python fix_aether.py --clean      # ... and also reset the Psiphon datastore
    python fix_aether.py --clean --path "C:\\Users\\me\\Downloads\\aether-windows-x86_64"
    python fix_aether.py --keep-app   # do not touch Clubapp VPN itself

Run it BEFORE starting Aether / Clubapp VPN.
"""
import argparse
import csv
import io
import os
import subprocess
import sys
import time

TARGETS = ["aether.exe", "psiphon-tunnel-core.exe", "lyrebird.exe",
           "tun2socks-windows-amd64.exe", "tun2socks.exe"]
PORTS = [8888, 10888, 10808, 10820, 10821, 1821]
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", creationflags=NOWIN)


def running(name):
    """PIDs of running processes called `name`."""
    r = run(["tasklist", "/FI", "IMAGENAME eq " + name, "/FO", "CSV", "/NH"])
    pids = []
    for row in csv.reader(io.StringIO(r.stdout)):
        if len(row) >= 2 and row[0].lower() == name.lower():
            try:
                pids.append(int(row[1]))
            except ValueError:
                pass
    return pids


def kill_all(names):
    total = 0
    for n in names:
        pids = running(n)
        if not pids:
            continue
        r = run(["taskkill", "/F", "/T", "/IM", n])
        ok = r.returncode == 0
        print("  [%s] %s  (%d process%s)" % ("OK " if ok else "ERR", n, len(pids),
                                            "" if len(pids) == 1 else "es"))
        if not ok and r.stderr.strip():
            print("        " + r.stderr.strip().splitlines()[0])
        total += len(pids) if ok else 0
    return total


def port_owners():
    """{port: pid} for listening TCP sockets on the ports we care about."""
    r = run(["netstat", "-ano", "-p", "TCP"])
    found = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING":
            try:
                port = int(parts[1].rsplit(":", 1)[1])
                if port in PORTS:
                    found[port] = int(parts[4])
            except (ValueError, IndexError):
                pass
    return found


def pid_name(pid):
    r = run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"])
    for row in csv.reader(io.StringIO(r.stdout)):
        if len(row) >= 2:
            return row[0]
    return "?"


def find_datastores(roots):
    hits = []
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dp, dns, fns in os.walk(root):
            if dp.count(os.sep) - base_depth > 5:
                dns[:] = []
                continue
            for f in fns:
                if f.lower().startswith("psiphon.boltdb"):
                    full = os.path.join(dp, f)
                    if full not in seen:
                        seen.add(full)
                        hits.append(full)
    return hits


def main():
    if os.name != "nt":
        print("This script is for Windows only.")
        return 1
    ap = argparse.ArgumentParser(description="Fix Aether/Psiphon datastore lock problems")
    ap.add_argument("--clean", action="store_true",
                    help="also delete the Psiphon datastore (psiphon.boltdb*)")
    ap.add_argument("--path", action="append", default=[],
                    help="extra folder to search for the datastore (repeatable)")
    ap.add_argument("--keep-app", action="store_true",
                    help="do not kill ClubappVPN.exe (it is never killed anyway; "
                         "kept for clarity)")
    args = ap.parse_args()

    print("== 1/3  Killing leftover Aether / Psiphon processes ==")
    n = kill_all(TARGETS)
    if n == 0:
        print("  nothing was running.")
    # wait until Windows really released the file locks
    for _ in range(10):
        if not any(running(t) for t in TARGETS):
            break
        time.sleep(0.5)
    left = [t for t in TARGETS if running(t)]
    if left:
        print("  WARNING: still running: %s\n"
              "  -> open CMD as Administrator and run this script again." % ", ".join(left))

    print("\n== 2/3  Checking proxy ports ==")
    owners = port_owners()
    if not owners:
        print("  all ports free: %s" % ", ".join(map(str, PORTS)))
    for port, pid in sorted(owners.items()):
        name = pid_name(pid)
        print("  port %d is used by PID %d (%s)" % (port, pid, name))
        if name.lower() in TARGETS:
            r = run(["taskkill", "/F", "/T", "/PID", str(pid)])
            print("    -> killed" if r.returncode == 0 else "    -> could not kill (run as Administrator)")
        else:
            print("    -> not an Aether process, left alone. Change the port in Settings "
                  "or close that program.")

    print("\n== 3/3  Psiphon datastore ==")
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.getcwd(), os.path.join(os.path.expanduser("~"), "Downloads"),
             os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")] + args.path
    # keep only the Aether / Clubapp related roots under AppData to stay fast and safe
    stores = find_datastores(roots)
    if not stores:
        print("  no psiphon.boltdb file found (nothing to reset).")
    for f in stores:
        if args.clean:
            try:
                os.remove(f)
                print("  deleted: " + f)
            except OSError as e:
                print("  could not delete %s (%s)" % (f, e))
        else:
            print("  found: " + f)
    if stores and not args.clean:
        print("  (use --clean to delete these and start Psiphon from a fresh database)")

    print("\nDone. Now start Aether / Clubapp VPN again.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
