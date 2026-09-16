#!/usr/bin/env python3
"""Poll a Tasmota plug's energy sensor over HTTP and append CSV rows.

Designed for AP-mode operation (no WiFi/MQTT): the plug serves 192.168.4.1
directly. The CSE7766 refreshes roughly once per second, so polling faster
than that repeats samples -- `seen_*` flags mark rows carrying fresh data.
"""
import argparse, csv, json, signal, sys, time, urllib.request

def fetch(host, timeout):
    url = f"http://{host}/cm?cmnd=Status%208"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())["StatusSNS"]["ENERGY"]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="192.168.4.1")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between polls")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl-C)")
    p.add_argument("--out", default="power_log.csv")
    p.add_argument("--label", default="", help="tag written into every row")
    p.add_argument("--timeout", type=float, default=4.0)
    a = p.parse_args()

    cols = ["ts_unix", "ts_iso", "label", "power_w", "apparent_va", "reactive_var",
            "factor", "voltage_v", "current_a", "total_kwh", "today_kwh", "fresh", "error"]
    start = time.time()
    n = fresh_n = errs = 0
    last_sig = None
    psum = pmin = pmax = None

    stop = False
    def on_sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        nxt = time.time()
        while not stop:
            now = time.time()
            if a.duration and now - start >= a.duration:
                break
            row = {c: "" for c in cols}
            row["ts_unix"] = f"{now:.3f}"
            row["ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)) + f".{int(now%1*1000):03d}"
            row["label"] = a.label
            try:
                e = fetch(a.host, a.timeout)
                # Total advances monotonically; pair it with the instantaneous
                # trio so a repeated serial frame is detectable.
                sig = (e.get("Total"), e.get("Power"), e.get("Voltage"), e.get("Current"))
                is_fresh = sig != last_sig
                last_sig = sig
                pw = float(e.get("Power", 0) or 0)
                row.update(power_w=pw, apparent_va=e.get("ApparentPower", ""),
                           reactive_var=e.get("ReactivePower", ""), factor=e.get("Factor", ""),
                           voltage_v=e.get("Voltage", ""), current_a=e.get("Current", ""),
                           total_kwh=e.get("Total", ""), today_kwh=e.get("Today", ""),
                           fresh=int(is_fresh))
                n += 1
                if is_fresh:
                    fresh_n += 1
                    psum = pw if psum is None else psum + pw
                    pmin = pw if pmin is None else min(pmin, pw)
                    pmax = pw if pmax is None else max(pmax, pw)
            except Exception as ex:
                errs += 1
                row["error"] = type(ex).__name__
            w.writerow(row)
            f.flush()
            nxt += a.interval
            time.sleep(max(0, nxt - time.time()))

    el = time.time() - start
    avg = (psum / fresh_n) if fresh_n else 0.0
    print(f"\nwrote {a.out}", file=sys.stderr)
    print(f"  elapsed     {el:.1f}s   polls {n}  fresh {fresh_n}  errors {errs}", file=sys.stderr)
    if fresh_n:
        print(f"  power W     avg {avg:.2f}   min {pmin:.2f}   max {pmax:.2f}", file=sys.stderr)
        print(f"  energy      {avg*el/3600:.4f} Wh (avg x elapsed)", file=sys.stderr)

if __name__ == "__main__":
    main()
