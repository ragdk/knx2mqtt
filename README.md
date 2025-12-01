# knx2mqtt
Small python package to get to learn working with KNX from "outside" using the same library that Home Assistant is using.

TODO
- [x] Listen to the KNX bus via KNX TCP/IP Secure router
- [x] Get the right datatypes
- [x] Publish data to MQTT broker
- [x] Get config and options into a file
- [x] Run the daemon in a docker container - now supports `docker compose up`
- [ ] Write to the KNX bus to make a change in a device, e.g. turn on/off the light
- [ ] Get command from MQTT broker and pass it on to KNX device

# Usage
Developing with uv (https://astral.sh/blog/uv)
- sync packages with uv
- run `uv run knx2mqtt --help` to get info.
- KNX IP Secure is enabled by default; set `Secure = false` in the config to use a non-secure tunnel (still requires `KNX_KEYS_PW` for opening the KNX project).
- In the terminal export env vars `KNX_KEYS_PW` with the KNX project password when connecting to a secure gateway.
- Run
  - Standalone: `uv run knx2mqtt --config=[path to config file]`.
  - With Docker and incl. MQTT broker: `docker compose up`
