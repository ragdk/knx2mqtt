# Repository Guidelines

## Project Structure & Modules
- `src/knx2mqtt/`: KNX-to-MQTT daemon (CLI `knx2mqtt`, helper `knxproj2json`).
- `src/knxmonitor/`: FastAPI/WebSocket monitor UI (CLI `knxmonitor`).
- `config*.ini`: Sample runtime configs; `config.ini` is the default mount target.
- `compose.yaml`: Docker compose for KNX daemon, MQTT broker, and monitor UI.
- `mosquitto/`: MQTT broker config bundled for compose.

## Build, Run, and Test
- Sync deps with uv: `uv sync` (Python 3.12+).
- Run KNX daemon locally: `uv run knx2mqtt --config config.ini` (env `KNX_KEYS_PW` required).
- Print KNX project JSON: `uv run knxproj2json --knx-project path/to/file.knxproj`.
- Run monitor UI: `uv run knxmonitor --mqtt-broker <host> --mqtt-main-topic knx --port 8000`.
- Containers: `docker compose up` (mount `.knxproj`/`.knxkeys` via `KNX_FILE_BASE_PATH`).
- Tests: none defined yet; add targeted unit tests alongside modules when introducing new logic.

## Coding Style & Naming
- Python 3.12; prefer type hints and descriptive names (avoid one-letter vars).
  - Use `uv` for python package and project managemnt.
- Follow existing logging format (`logging.basicConfig` in CLIs); keep messages actionable.
- CLI options use `click`; maintain lowercase dashed option names and clear help text.
- Keep modules cohesive: KNX/MQTT logic under `knx2mqtt`, UI/monitor logic under `knxmonitor`.

## Testing Guidelines
- Place tests mirroring package paths (e.g., `tests/knx2mqtt/test_mqtt.py`).
- Use `pytest` if added; name files `test_*.py` and fixtures in `conftest.py`.
- For MQTT/KNX interactions, prefer small unit tests with fakes over live network.
- Document any required env vars (`KNX_KEYS_PW`, broker host) in test docstrings.

## Commit & PR Practices
- Commit messages: imperative, short summary (e.g., "Add KNX monitor startup hooks").
- PRs should describe scope, configs touched, and validation (manual runs or tests).
- Link related issues; include screenshots or CLI output for UI/CLI changes.
- Note security-sensitive configs (keys paths, passwords) and avoid committing secrets.

## Security & Configuration Tips
- Never commit `.knxproj` or `.knxkeys`; mount at runtime as read-only.
- Set `KNX_KEYS_PW` in env for both daemon and monitor when accessing secured projects.
- Use `Secure = false` in config only for non-secure gateways; keep keys path consistent with compose mounts.
