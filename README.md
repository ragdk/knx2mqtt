# knx2mqtt
Small python package to get to learn working with KNX from "outside" using the same library that Home Assistant is using.

TODO
- [x] Listen to the KNX bus via KNX TCP/IP Secure router
- [x] Get the right datatypes
- [x] Publish data to MQTT broker
- [ ] Write to the KNX bus to make a change in a device, e.g. turn on/off the light
- [ ] Get command from MQTT broker and pass it on to KNX device
- [x] Get config and options into a file
- [ ] Run the daemon in a docker container

# Usage
Developing with uv (https://astral.sh/blog/uv)
- sync packages with uv
- run `uv run knx2mqtt --help` to get info.
- In the terminal export env vars `KNX_KEYS_PW` with the KNX project password.
- Run: `uv run knx2mqtt --config=[path to config file]`.