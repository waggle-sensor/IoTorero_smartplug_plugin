# IoTorero_smartplug_plugin

This plugin reads the power measurements from the smart plug and publishes them
to beehive: a Sage edge app for energy profiling of edge compute hardware,
backed by a Tasmota-flashed smart plug polled over HTTP.

The plug runs as a WiFi access point and never joins a real network, so no
credentials are handed to it and no MQTT broker is involved. The node reaches
it over a USB WiFi adapter joined to the plug's own AP.

## Layout

| Path | Purpose |
|---|---|
| `app/main.py` | the edge app: polls the plug, publishes Waggle measurements |
| `scripts/setup.py` | one-time plug configuration; run before deploying the app |
| `scripts/node_wifi_setup.sh` | node-side USB WiFi driver install and AP connection |
| `scripts/tasmota_log.py` | standalone CSV logger for benchtop runs |
| `Dockerfile`, `requirements.txt`, `sage.yaml` | app packaging and metadata |
| `ecr-meta/` | ECR science description |

Only `app/` and the packaging files ship in the container; `scripts/` is
excluded by `.dockerignore` because it configures hardware, not the app.

## Bring-up order

The three pieces are set up once each, in this order.

### 1. Configure the plug

Run from a machine joined to the plug's WiFi AP:

    python3 scripts/setup.py --node-vsn k002

It raises the measurement resolution (stock firmware quantises power to whole
watts), disables dynamic CPU sleep so serial frames from the energy chip are
not missed, and names the device `sgt-smartplug-<mac_suffix>-<node_vsn>`, with
the MAC suffix read off the plug so a second unit names itself correctly.

Changing the hostname also renames the broadcast AP and restarts the plug, so
the machine running this will drop off and must rejoin under the new name.
That step is applied last and on its own; `--skip-hostname` avoids it. Running
again once the plug matches is a no-op, and `--dry-run` reports without
sending.

### 2. Set up the node's WiFi adapter

The BrosTrend AC-series USB adapters are rebadged Realtek `0bda:c811` parts.
That chipset was never mainlined and NVIDIA's L4T kernel ships no Realtek
wireless modules at all, so the driver is built out of tree and registered
with DKMS to survive kernel updates. On the node, as root:

    ./scripts/node_wifi_setup.sh --ssid sgt-smartplug-4d12e9-k002

| Flag | Effect |
|---|---|
| *(none)* | install the driver only, then print how to connect |
| `--ssid <name>` | also join that access point |
| `--psk <password>` | for a secured AP; omit entirely for an open one |
| `--dry-run` | report what would change, send nothing |
| `--disconnect` | remove the `smartplug` connection, leave the driver |
| `--uninstall` | remove the DKMS module |

The connection is created with `ipv4.never-default` and `ipv6.never-default`,
a high route metric, and `ignore-auto-dns`. This matters: the plug's network
has no path to Sage, so letting it win the default route would cut the node
off with no way back in. The script records the default route before
connecting, re-checks it after, and rolls back automatically if it changed. A
watchdog also tears the connection down if the run never reaches its success
check, so a wedged node recovers on its own.

Re-running is safe at any point, including while already connected.

### 3. Run the app

    sudo pluginctl run --name smartplug-power $(sudo pluginctl build .)

## Running the app locally

    pip3 install -r requirements.txt
    PYWAGGLE_LOG_DIR=test-run python3 app/main.py

Publishes land in `test-run/data.ndjson`. Without `PYWAGGLE_LOG_DIR`, and off
node, they go nowhere and pywaggle logs a harmless rabbitmq connection error.

    python3 app/main.py --once --debug      # one sample, verbose
    python3 app/main.py --host 192.168.4.1  # plug address (default)
    python3 app/main.py --interval 1.0      # poll period

`PLUG_HOST` and `PLUG_INTERVAL` work as environment equivalents.

## Published measurements

| Name | Units |
|---|---|
| `env.power.watts` | W, active power |
| `env.power.apparent_va` | VA |
| `env.power.reactive_var` | var |
| `env.power.factor` | power factor, 0-1 |
| `env.power.voltage_volts` | V |
| `env.power.current_amps` | A |
| `env.power.energy_total_kwh` | kWh, lifetime counter |
| `env.power.energy_today_kwh` | kWh, resets daily |

Each carries `sensor` and `host` metadata.

## Measurement notes

- **~1 Hz ceiling.** The CSE7766 energy chip refreshes about once per second.
  Transients shorter than that are invisible at any polling rate, so treat the
  data as steady-state averages rather than peak capture.
- **Prefer the energy counters.** `env.power.energy_total_kwh` is integrated in
  hardware and does not miss the gaps between polls. Differencing it is more
  accurate than integrating `env.power.watts`; on a fluctuating load the
  sampled integral ran about 2% low in testing.
- **Calibration.** Readings are only as good as the plug's calibration
  constants. Calibrate against a known resistive load (a ~60 W incandescent
  bulb) with Tasmota's `VoltageSet`/`PowerSet` before reporting absolute
  figures.
- **Wall power, not board power.** Measurements include PSU conversion losses,
  typically 10-15% above the device's own draw.

## Benchtop CSV logging

Independent of Waggle, for bench work before a node is involved:

    python3 scripts/tasmota_log.py --duration 300 --interval 1.0 \
        --label thor-idle --out thor_idle.csv
