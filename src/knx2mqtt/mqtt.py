import sys
import logging
import datetime
import json
from typing import Callable

import paho.mqtt.client as mqtt

# Configure logging
logger = logging.getLogger(__name__)

class MQTTClient:
    def __init__(self, broker: str, port: int, mqtt_client_id: str, main_topic: str, on_command: Callable[[dict], None] | None = None):
        self.broker = broker
        self.port = port
        self.pub_topic = f"{main_topic}/data"
        self.cmd_topic = f"{main_topic}/cmd"
        self.is_running = False
        self.on_command = on_command
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5, client_id=mqtt_client_id) # pyright: ignore[reportPrivateImportUsage]
        self.client.enable_logger()

        # Set callbacks
        self.client.on_message = self.__on_message
        self.client.on_connect = self.__on_connect
        self.client.on_disconnect = self.__on_disconnect
        self.client.on_publish = self.__on_publish

        try:
            self.client.connect(self.broker, self.port)
        except ConnectionError as e:
            logger.error(f"Failed to connect to MQTT broker {self.broker}:{self.port}: {e}")
            exit(1)
        logger.info(f"Connected to MQTT broker {self.broker}:{self.port}")

    def __on_message(self, client, userdata, msg):
        logger.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
        if msg.topic != self.cmd_topic or not self.on_command:
            return
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as exc:
            logger.warning(f"Failed to parse command payload: {exc}")
            return
        try:
            self.on_command(payload)
        except Exception as exc:
            logger.error(f"Command handler failed: {exc}")

    def __on_publish(self, client, userdata, mid, reason_code, properties):
        if reason_code.is_failure:
            logger.error(f"Failed to publish message: {reason_code}.")
        else:
            logger.debug(f"Message published successfully, ID: {mid}.")

    def __on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error(f"Failed to connect to broker '{self.broker}:{self.port}': {reason_code}.")
            self.disconnect()
        else:
            self.is_running = True
            # Subscribe to the specified topic
            self.client.subscribe(self.cmd_topic)
            logger.info(f"Listening to topic '{self.cmd_topic}' on broker '{self.broker}:{self.port}'")

    def __on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        logger.info(f"Disconnected from broker: {reason_code}.")
        self.is_running = False

    def publish(
        self,
        deviceid,
        type,
        unit,
        value,
        destination,
        direction=None,
        destination_name=None,
        device_name=None,
        knx_message_type=None,
    ):
        # TODO: This is ust fire-and-forget, consider using QoS and error handling
        # topic = f"{self.pub_topic}/{deviceid}/{type}"
        topic = self.pub_topic
        try: # the value may not be JSON serializable
            json.dumps(value)
        except TypeError: # if so, convert to string
            value = str(value)
        msg = {
            "deviceid": deviceid,
            "timestamp": datetime.datetime.now().isoformat(),
            "destination": destination,
            "type": type,
            "unit": unit,
            "value": value,
            "direction": direction,
            "destination_name": destination_name,
            "device_name": device_name,
            "knx_message_type": knx_message_type,
        }
        self.client.publish(topic, json.dumps(msg, ensure_ascii=False))

    def run(self):
        logging.basicConfig(format="{asctime}: {levelname:<7}: {name:<17}: {message}", style="{", datefmt="%Y-%m-%d %H:%M", force=True)
        logger.info(f"Running MQTT client - broker {self.broker}:{self.port}...")
        # Start the loop to process messages
        self.client.loop_start()
        self.is_running = True

    def disconnect(self):
        logger.info("Disconnecting from broker...")
        self.client.loop_stop()
        self.client.disconnect()

# Example usage
if __name__ == "__main__":
    logging.basicConfig(format="{asctime}: {levelname:<7}: {name:<17}: {message}", style="{", datefmt="%Y-%m-%d %H:%M", force=True)
    logging.getLogger(__name__).setLevel(logging.DEBUG)

    mqtt_client = MQTTClient("localhost", 1883, "test_client", "knx")
    mqtt_client.run()

    try:
        # Keep the script running
        while mqtt_client.is_running:
            pass
    except KeyboardInterrupt:
        mqtt_client.disconnect()
