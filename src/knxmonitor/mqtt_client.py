import asyncio
import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MonitorMQTTClient:
    """MQTT subscriber that forwards messages into an asyncio queue."""

    def __init__(
        self,
        broker: str,
        port: int,
        client_id: str,
        main_topic: str,
        loop: asyncio.AbstractEventLoop,
        message_queue: asyncio.Queue,
    ):
        self.broker = broker
        self.port = port
        self.client_id = client_id
        self.main_topic = main_topic
        self.sub_topic = f"{main_topic}/data"
        self.cmd_topic = f"{main_topic}/cmd"
        self.loop = loop
        self.message_queue = message_queue

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, # pyright: ignore[reportPrivateImportUsage]
            protocol=mqtt.MQTTv5,
            client_id=self.client_id,
        )
        self.client.enable_logger(logger)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def start(self):
        """Connect and start the MQTT network loop."""
        logging.basicConfig(format="{asctime}: {levelname:<7}: {name:<17}: {message}", style="{", datefmt="%Y-%m-%d %H:%M", force=True)
        try:
            self.client.connect(self.broker, self.port)
        except ConnectionError as exc:
            logger.error("Failed to connect to MQTT broker %s:%s: %s", self.broker, self.port, exc)
            raise
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    # MQTT callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("Failed to connect to broker '%s:%s': %s", self.broker, self.port, reason_code)
            return
        logger.info("Connected to MQTT broker %s:%s, subscribing to %s", self.broker, self.port, self.sub_topic)
        self.client.subscribe(self.sub_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if reason_code.is_failure:
            logger.warning("Unexpected disconnect from MQTT broker: %s", reason_code)
        else:
            logger.info("Disconnected from MQTT broker.")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode()
            data = json.loads(payload)
        except Exception as exc:
            logger.warning("Dropping malformed MQTT message on %s: %s (%s)", msg.topic, msg.payload, exc)
            return

        # Push into asyncio queue from MQTT thread
        try:
            self.loop.call_soon_threadsafe(self._queue_message, data)
        except RuntimeError as exc:
            logger.error("Failed to forward MQTT message to event loop: %s", exc)

    def _queue_message(self, message: dict):
        try:
            self.message_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Monitor queue full; dropping MQTT message")

    def request_group_reads(self, group_addresses: list[str]):
        """Publish a read request for the given KNX group addresses."""
        if not group_addresses:
            return
        payload = json.dumps({"action": "read", "destinations": group_addresses})
        result = self.client.publish(self.cmd_topic, payload)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error("Failed to publish read request to %s: %s", self.cmd_topic, result)
        else:
            logger.info("Published KNX read request for %s", group_addresses)
