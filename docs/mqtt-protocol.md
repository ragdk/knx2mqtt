# KNX MQTT Protocol (Draft v1.0)

Goal: loosely couple `knx2mqtt` and `knxmonitor` using a stable MQTT contract.

## Topic layout

All topics are namespaced under a configurable main topic (default: `knx`).

- Bus data: `{main}/busdata`
- Commands: `{main}/cmd`

Replies to read commands are observed on the KNX bus
and will arrive as regular bus data, including responses from other devices.

## Common envelope

All messages share the same top-level envelope:

```json
{
  "schema_version": "1.0",
  "message_type": "knx.event",
  "message_id": "5f2b7c2e-9a85-4b0c-b6a5-2f5b0c6dbe6b",
  "timestamp": "2025-01-12T12:30:12.123Z",
  "source": {
    "service": "knx2mqtt",
    "instance_id": "knx2mqtt-01"
  },
  "payload": {}
}
```

Field notes:
- `schema_version`: protocol version, `1.0` for this draft.
- `message_type`: identifies event/command type.
- `message_id`: UUID for the message.
- `timestamp`: UTC time; always send in RFC3339 with `Z`.
- `source`: origin service name and optional instance id.
- `payload`: type-specific payload.

## Bus data

Topic: `{main}/busdata`

`message_type`: `knx.event`

Payload fields:
- `device_id` (string, required)
- `destination` (string, required)
- `value` (any, optional)
- `unit` (string, optional)
- `direction` (string, optional)
- `device_name` (string, optional)
- `destination_name` (string, optional)
- `knx_message_type` (string, optional)
- `dpt_main` (int, optional)
- `dpt_sub` (int, optional)

Example:

```json
{
  "schema_version": "1.0",
  "message_type": "knx.event",
  "message_id": "f2f1a5a8-76d3-48b7-9e6d-0ce8d6b9c7b7",
  "timestamp": "2025-01-12T12:30:12.123Z",
  "source": { "service": "knx2mqtt", "instance_id": "knx2mqtt-01" },
  "payload": {
    "device_id": "1.1.10",
    "destination": "2/3/4",
    "value": 21.4,
    "unit": "C",
    "direction": "incoming",
    "device_name": "Touch panel",
    "destination_name": "Living room temperature",
    "knx_message_type": "GROUP_RESPONSE",
    "dpt_main": 9,
    "dpt_sub": 1
  }
}
```

## Commands

Topic: `{main}/cmd`

`message_type`:
- `knx.command.read`
- `knx.command.write`

Read payload:
- `action`: `knx.command.read`
- `destinations`: list of group addresses

Write payload:
- `action`: `knx.command.write`
- `destination`: single group address
- `value`: value to send
- `dpt_main` / `dpt_sub`: optional; helps encode value

Example (read):

```json
{
  "schema_version": "1.0",
  "message_type": "knx.command.read",
  "message_id": "7e23e8bd-1d02-4a35-9f97-6fd9f1a4e1f0",
  "timestamp": "2025-01-12T12:31:12.123Z",
  "source": { "service": "knxmonitor", "instance_id": "knxmonitor-01" },
  "payload": {
    "action": "knx.command.read",
    "destinations": ["2/3/4", "2/3/5"]
  }
}
```

## Compatibility notes

- Consumers should reject unknown `schema_version`.
- Producers should not remove fields; only add optional fields.
