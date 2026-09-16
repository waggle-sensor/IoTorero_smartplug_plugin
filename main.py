"""Sage edge app: publish power measurements from a Tasmota smart plug.

The plug runs in WiFi AP mode and serves its readings over plain HTTP at
192.168.4.1, so this app needs no MQTT or broker -- it polls `Status 8` and
republishes the fields as Waggle measurements.

Run `setup.py` against the plug before deploying this; it sets the resolution
and sampling options these measurements assume.
"""
import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from waggle.plugin import Plugin

# Tasmota ENERGY field -> published measurement name. Names must be [a-z0-9_]
# joined by '.', per pywaggle's raise_for_invalid_publish_name.
MEASUREMENTS = [
    ("Power",         "env.power.watts",           float),
    ("ApparentPower", "env.power.apparent_va",     float),
    ("ReactivePower", "env.power.reactive_var",    float),
    ("Factor",        "env.power.factor",          float),
    ("Voltage",       "env.power.voltage_volts",   float),
    ("Current",       "env.power.current_amps",    float),
    # Hardware-integrated energy counter. Preferred over integrating Power
    # yourself: the chip accumulates continuously, so it does not miss the
    # gaps between polls.
    ("Total",         "env.power.energy_total_kwh", float),
    ("Today",         "env.power.energy_today_kwh", float),
]


def fetch(host, timeout):
    """Return the plug's ENERGY block, or raise."""
    url = "http://%s/cm?cmnd=%s" % (host, urllib.parse.quote("Status 8"))
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body)["StatusSNS"]["ENERGY"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=os.getenv("PLUG_HOST", "192.168.4.1"),
                   help="plug address (default 192.168.4.1, its AP-mode IP)")
    p.add_argument("--interval", type=float, default=float(os.getenv("PLUG_INTERVAL", "1.0")),
                   help="seconds between polls; the CSE7766 only refreshes ~1 Hz")
    p.add_argument("--timeout", type=float, default=4.0)
    p.add_argument("--once", action="store_true", help="publish one sample and exit")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Meta values must all be strings (pywaggle's valid_meta).
    meta = {"sensor": "IoTorero_Smart_Plug", "host": a.host}

    logging.info("polling %s every %.2fs", a.host, a.interval)

    with Plugin() as plugin:
        nxt = time.time()
        while True:
            # Timestamp the reading, not the publish: the loop below can stall
            # on a slow HTTP response and we want the sample's own time.
            ts = time.time_ns()
            try:
                energy = fetch(a.host, a.timeout)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                # A plug reboot or a dropped AP association should not kill a
                # long deployment -- log it and keep trying.
                logging.warning("read failed: %s", e)
            else:
                for field, name, cast in MEASUREMENTS:
                    if field not in energy:
                        continue
                    try:
                        value = cast(energy[field])
                    except (TypeError, ValueError):
                        logging.warning("field %s not numeric: %r", field, energy[field])
                        continue
                    plugin.publish(name, value, meta=meta, timestamp=ts)
                logging.debug("published %.2f W", float(energy.get("Power", 0)))

            if a.once:
                return
            nxt += a.interval
            time.sleep(max(0, nxt - time.time()))


if __name__ == "__main__":
    main()
