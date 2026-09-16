# Smart Plug Power Measurement

## Science

Measuring the wall-plug power of edge compute hardware (e.g. an NVIDIA Jetson
Thor) is the basis for energy-per-inference and performance-per-watt analysis.
This app turns a commodity Tasmota-flashed smart plug into a Waggle sensor,
publishing AC power, voltage, current and cumulative energy alongside whatever
else the node is recording.

## Method

The plug (Athom Plug V2 class hardware, CSE7766 energy monitor) runs in WiFi
access-point mode and exposes its readings over plain HTTP, so no MQTT broker
or network credentials are involved. The app polls the Tasmota `Status 8`
command and republishes each ENERGY field as a Waggle measurement.

`setup.py` in this repository configures the plug before deployment: it raises
the reporting resolution (stock firmware quantises power to whole watts),
disables dynamic CPU sleep so serial frames from the energy chip are not
missed, and names the device consistently.

## Measurements

| Name | Units | Notes |
|---|---|---|
| `env.power.watts` | W | active power |
| `env.power.apparent_va` | VA | apparent power |
| `env.power.reactive_var` | var | reactive power |
| `env.power.factor` | - | power factor, 0-1 |
| `env.power.voltage_volts` | V | mains RMS voltage |
| `env.power.current_amps` | A | RMS current |
| `env.power.energy_total_kwh` | kWh | hardware-integrated lifetime counter |
| `env.power.energy_today_kwh` | kWh | hardware counter, resets daily |

Each measurement carries `sensor` and `host` metadata.

## Limitations

- **Sampling ceiling.** The CSE7766 refreshes roughly once per second, so
  transients shorter than ~1 s are not observable at any polling rate. The
  data are steady-state averages, not peak capture.
- **Calibration.** Readings are only as accurate as the plug's calibration
  constants. Calibrate against a known resistive load (a ~60 W incandescent
  bulb) with `VoltageSet`/`PowerSet` before reporting absolute figures.
- **Wall power, not board power.** Measurements include PSU conversion losses,
  typically 10-15% above the device's own draw.
- **Energy counters.** Prefer differencing `env.power.energy_total_kwh` over
  integrating `env.power.watts`: the chip accumulates continuously and does
  not miss the gaps between polls.
