# IoTorero_smartplug_plugin

This plugin reads the power measurements from the smart plug and publishes them
to beehive: a Sage edge app for energy profiling of edge compute hardware,
backed by a Tasmota-flashed smart plug polled over HTTP.

## Layout

| File | Purpose |
|---|---|
| `main.py` | the edge app: polls the plug, publishes Waggle measurements |
| `setup.py` | one-time plug configuration; run before deploying the app |
| `tasmota_log.py` | standalone CSV logger for local benchtop runs |
| `Dockerfile`, `requirements.txt`, `sage.yaml` | app packaging and metadata |
| `ecr-meta/` | ECR science description |

## Configure the plug first

`setup.py` is not part of the container. Run it from a machine joined to the
plug's WiFi AP, before the app is deployed:

    python3 setup.py --node-vsn k002

It applies the resolution and sampling settings the published measurements
assume, and names the device `sgt-smartplug-<mac_suffix>-<node_vsn>`. Changing
the hostname also renames the AP, so the machine running it must rejoin
afterwards. Re-running is a safe no-op once the plug matches.

## Run the app locally

    pip3 install -r requirements.txt
    PYWAGGLE_LOG_DIR=test-run python3 main.py --node-vsn k002

Publishes are written to `test-run/data.ndjson`. Without `PYWAGGLE_LOG_DIR`
and off-node, publishes go nowhere and pywaggle logs a harmless rabbitmq
connection error.

    python3 main.py --once --debug          # one sample, verbose
    python3 main.py --host 192.168.4.1      # non-default plug address
    python3 main.py --interval 1.0          # poll period (~1 Hz is the sensor limit)

## Run on a node

    sudo pluginctl run --name smartplug-power $(sudo pluginctl build .)

The node must be able to reach the plug. In AP mode the plug serves
`192.168.4.1` on its own network, so the node needs an interface joined to
that AP; otherwise point `--host` at the plug's address on your network.
