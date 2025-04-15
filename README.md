# knx2mqtt
Small python package to get to learn working with KNX from "outside" using the same library that Home Assistant is using.

TODO
- [x] Listen to the KNX bus via KNX TCP/IP Secure router
- [x] Get the right datatypes
- [x] Publish data to MQTT broker
- [ ] Write to the KNX bus to make a change in a device, e.g. turn on/off the light
- [ ] Get command from MQTT broker and pass it on to KNX device

# Usage
Developing with uv (https://astral.sh/blog/uv)
- sync packages with uv
- In the terminal export env vars `KNX_KEYS_PATH` and `KNX_KEYS_PW`.
- run `uv run knx2mqtt --help` to get info.
- to tun: `uv run knx2mqtt --gateway="[IP of KNX secure router]" --knx-project=[path to KNX project file]`.