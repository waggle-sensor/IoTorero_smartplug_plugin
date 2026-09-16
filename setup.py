#!/usr/bin/env python3
"""Apply the measurement settings and naming this project depends on.

Idempotent: reads the current value of each setting first and only sends the
ones that differ, so re-running is cheap and avoids needless restarts. Some
settings reboot the device; rather than guess which, the script compares
uptime across the apply and waits the device out whenever it dropped.

Naming is derived from the device rather than hardcoded, so this works
unchanged against a second plug:

    sgt-smartplug-<mac_suffix>-<node_vsn>     e.g. sgt-smartplug-4d12e9-0000

The name is lowercased throughout: hostnames are case-insensitive anyway,
and it keeps log filenames and URLs built from it consistent.

`Hostname` is special -- it also renames the broadcast AP SSID and restarts,
which drops this machine off the plug's network. It is therefore applied last,
on its own, after everything else is verified. Use --skip-hostname to keep the
current SSID.

Assumes the plug in AP mode at 192.168.4.1 -- no WiFi or MQTT involved.
"""
import argparse, json, sys, time, urllib.error, urllib.parse, urllib.request

NAME_PREFIX = "sgt-smartplug"
TASMOTA_NAME_LIMIT = 32          # DeviceName, FriendlyName and Hostname all cap here

# (command, value, why) -- measurement settings, none of which touch the network
SETTINGS = [
    ("WattRes",    "2",  "stock 0 quantises power to whole watts, hiding small deltas"),
    ("VoltRes",    "1",  "0.1 V resolution for mains sag under load"),
    ("AmpRes",     "3",  "mA resolution"),
    ("EnergyRes",  "5",  "fine kWh accumulation for short captures"),
    ("TelePeriod", "10", "stock 300 s telemetry is far too coarse"),
    ("Sleep",      "0",  "dynamic sleep naps the CPU between CSE7766 serial frames"),
]


def cmnd(host, command, timeout=6.0):
    url = "http://%s/cm?cmnd=%s" % (host, urllib.parse.quote(command))
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body}


def current(host, name, timeout=6.0):
    """Return a setting's value as a string, or None if unreadable.

    Most settings answer {"Name": value}, but a few (Sleep, SerialLog) answer
    {"Name": {"<value>": {"Active": "<value>"}}} instead. Note that `Hostname`
    reports the stored template (default "%s-%04d"), not the expanded name --
    which is what we want to compare against.
    """
    try:
        d = cmnd(host, name, timeout)
    except (urllib.error.URLError, OSError):
        return None
    if name not in d:
        return None
    v = d[name]
    if isinstance(v, dict):
        return str(next(iter(v)))
    return str(v)


def uptime(host, timeout=6.0):
    """Seconds since boot, or None if unreadable."""
    try:
        return cmnd(host, "Status 11", timeout)["StatusSTS"]["UptimeSec"]
    except (urllib.error.URLError, OSError, KeyError, TypeError):
        return None


def wait_online(host, timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if current(host, "WattRes", timeout=3.0) is not None:
            return True
        time.sleep(1.5)
    return False


def mac_suffix(net):
    """Last three MAC bytes, e.g. F4:2D:C9:4D:12:E9 -> 4D12E9.

    This is the identity Tasmota already uses for its own default hostname and
    MQTT topic, so names built from it line up with the device's own labelling.
    The plug exposes no factory serial over the HTTP API.
    """
    mac = (net.get("Mac") or "").replace(":", "").upper()
    return mac[-6:] if len(mac) >= 6 else None


def apply(host, pending, timeout):
    """Send pending (command, value) pairs as one Backlog. True if it stuck."""
    before = uptime(host, timeout)
    # ';' must be %3B-encoded, which urllib.parse.quote handles since it is
    # not in its safe set.
    backlog = "Backlog " + "; ".join("%s %s" % (n, v) for n, v in pending)
    try:
        cmnd(host, backlog, timeout)
    except (urllib.error.URLError, OSError):
        # A restarting device can cut the reply off mid-flight, which is not a
        # failure; wait_online below decides whether it really went away.
        pass
    time.sleep(2.0)
    if not wait_online(host):
        return False
    after = uptime(host, timeout)
    if before is not None and after is not None and after < before:
        print("  device restarted (uptime %ss -> %ss)" % (before, after))
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="192.168.4.1")
    p.add_argument("--node-vsn", default="0000", help="Sage node vsn")
    p.add_argument("--dry-run", action="store_true", help="report what would change, send nothing")
    p.add_argument("--skip-hostname", action="store_true",
                   help="leave the hostname (and therefore the AP SSID) alone")
    p.add_argument("--timeout", type=float, default=6.0)
    a = p.parse_args()

    print("device %s" % a.host)
    try:
        st = cmnd(a.host, "Status 0", a.timeout)
    except (urllib.error.URLError, OSError) as e:
        print("  unreachable: %s" % e, file=sys.stderr)
        print("  is this machine still joined to the plug's AP?", file=sys.stderr)
        return 2

    fwr, net = st.get("StatusFWR", {}), st.get("StatusNET", {})
    print("  %s  Tasmota %s" % (net.get("Hostname", "?"), fwr.get("Version", "?")))
    if net.get("IPAddress") in (None, "0.0.0.0"):
        print("  AP mode (no station IP) -- HTTP polling only, MQTT unavailable")

    suffix = mac_suffix(net)
    if not suffix:
        print("  could not read MAC -- cannot derive names", file=sys.stderr)
        return 2
    name = ("%s-%s-%s" % (NAME_PREFIX, suffix, a.node_vsn)).lower()
    print("  name: %s  (MAC suffix %s, node %s)" % (name, suffix, a.node_vsn))

    if len(name) > TASMOTA_NAME_LIMIT:
        print("  name is %d chars, over Tasmota's %d limit -- shorten --node-vsn"
              % (len(name), TASMOTA_NAME_LIMIT), file=sys.stderr)
        return 2
    if "%" in name:
        # Tasmota silently reverts a hostname containing '%' to the default.
        print("  name contains '%', which Tasmota would reject", file=sys.stderr)
        return 2

    safe = SETTINGS + [
        ("DeviceName",    name, "identifies the plug in the web UI"),
        ("FriendlyName1", name, "label used for the power/energy readings"),
    ]

    pending = []
    print("\nsettings")
    for cmd, want, why in safe:
        have = current(a.host, cmd, a.timeout)
        if have is None:
            print("  %-13s ? unreadable -- will send anyway" % cmd)
            pending.append((cmd, want))
        elif have == want:
            print("  %-13s %-26s ok" % (cmd, have))
        else:
            print("  %-13s %-26s -> %s" % (cmd, have, want))
            print("  %-13s   (%s)" % ("", why))
            pending.append((cmd, want))

    # Hostname is read and reported with the rest, but never applied alongside
    # them: it renames the AP, so it goes last and on its own.
    host_now = current(a.host, "Hostname", a.timeout)
    host_pending = (not a.skip_hostname) and host_now != name
    if a.skip_hostname:
        print("  %-13s %-26s skipped (--skip-hostname)" % ("Hostname", host_now))
    elif host_pending:
        print("  %-13s %-26s -> %s" % ("Hostname", host_now, name))
        print("  %-13s   (renames the AP SSID and restarts -- applied last)" % "")
    else:
        print("  %-13s %-26s ok" % ("Hostname", host_now))

    if not pending and not host_pending:
        print("\nnothing to change.")
        return 0
    if a.dry_run:
        print("\ndry run: %d setting(s) would change%s."
              % (len(pending) + host_pending,
                 ", including the hostname/SSID" if host_pending else ""))
        return 0

    if pending:
        print("\napplying %d setting(s)" % len(pending))
        if not apply(a.host, pending, a.timeout):
            print("  device did not come back -- rejoin the AP and re-run", file=sys.stderr)
            return 1

        print("\nverifying")
        bad = 0
        for cmd, want, _ in safe:
            have = current(a.host, cmd, a.timeout)
            ok = have == want
            bad += not ok
            print("  %-13s %-26s %s" % (cmd, have, "ok" if ok else "EXPECTED %s" % want))
        if bad:
            print("\n%d setting(s) did not stick." % bad, file=sys.stderr)
            return 1

    if host_pending:
        print("\napplying Hostname -- the AP will be renamed to '%s'" % name)
        print("  this machine will drop off '%s' and must rejoin"
              % (net.get("Hostname") or "the current SSID"))
        try:
            cmnd(a.host, "Hostname %s" % name, a.timeout)
        except (urllib.error.URLError, OSError):
            pass  # expected: the reply is cut off by the restart
        time.sleep(3.0)
        if wait_online(a.host, timeout=20.0):
            # Still reachable, so this machine followed the rename (or the AP
            # kept the association); verify properly.
            have = current(a.host, "Hostname", a.timeout)
            print("  Hostname %s %s" % (have, "ok" if have == name else "EXPECTED %s" % name))
            return 0 if have == name else 1
        print("  device is off-network, as expected after the SSID rename.")
        print("  rejoin WiFi '%s', then re-run to verify:" % name)
        print("    python3 setup.py --node-vsn %s" % a.node_vsn)
        return 0

    print("\nready. capture with:")
    print("  python3 tasmota_log.py --duration 300 --interval 1.0 --label thor-idle --out thor_idle.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
