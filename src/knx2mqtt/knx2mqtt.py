import os, sys
import asyncio
import click
import json
import enum
import logging

from configparser import ConfigParser, NoOptionError

from .mqtt import MQTTClient

from xknx import XKNX
from xknx.dpt.dpt import DPTBase
from xknx.exceptions import CouldNotParseTelegram, CommunicationError
from xknx.telegram import Telegram, AddressFilter
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, APCIService, APCI
from xknx.devices import Device
from xknx.core.connection_state import XknxConnectionState
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknx.tools.group_communication import group_value_read
from xknxproject.models.knxproject import KNXProject
from xknxproject.xknxproj import XKNXProj

# Configure logging
logger = logging.getLogger(__name__)

class GroupAddressInfo:
    """Group address info for runtime usage."""
    address: str
    name: str
    description: str
    dpt_main: int | None
    dpt_sub: int | None
    transcoder: type[DPTBase] | None

    def __init__(self, address: str, name: str, description: str, dpt_main: int, dpt_sub: int|None):
        self.address = address
        self.name = name
        self.description = description
        self.dpt_main = dpt_main
        self.dpt_sub = dpt_sub
        self.transcoder = DPTBase.transcoder_by_dpt(dpt_main, dpt_sub)

class KNXDaemon:
    """KNX daemon.
    Listens to the KNX bus and publishes messages to an MQTT broker."""

    def __init__(self, 
                 knx_gateway: str, 
                 knx_project_path: str | os.PathLike, 
                 knx_keys_path: str | os.PathLike | None,
                 knxkeys_pw: str | None,
                 knx_secure: bool = True,
                 mqtt_broker: str | None= None,
                 mqtt_port: int | None = None,
                 mqtt_client_id: str | None = None,
                 mqtt_main_topic: str = "knx",
                 rooms_to_monitor: list[str] = [], 
                 ):
        # Input checks
        do_exit = False
        if not os.path.exists(knx_project_path):
            logger.error(f"KNX project file {knx_project_path} does not exist.")
            do_exit = True
        if not knxkeys_pw:
            logger.error("Please provide KNX keys password via the KNX_KEYS_PW environment variable.")
            do_exit = True

        self.secure_enabled = knx_secure
        if self.secure_enabled:
            if not knx_keys_path:
                logger.error("KNX_KEYS_PW is set but no KeysPath provided in config.")
                do_exit = True
            elif not os.path.exists(knx_keys_path):
                logger.error(f"KNX keys file {knx_keys_path} does not exist.")
                do_exit = True
        elif knx_keys_path and not os.path.exists(knx_keys_path):
            logger.warning(f"KeysPath provided but KNX IP Secure disabled via config; ignoring missing keys file {knx_keys_path}.")
        if do_exit:
            exit(1)

        # MQTT client - do this first as it is quickest
        self.__setup_mqtt(mqtt_broker, mqtt_port, mqtt_client_id, mqtt_main_topic)
        # KNX
        self.__setup_knx(knx_gateway, knx_project_path, knx_keys_path, knxkeys_pw, rooms_to_monitor)

    def __setup_knx(self, knx_gateway, knx_project_path, knx_keys_path, knxkeys_pw, rooms_to_monitor):
        self.knx_keys_path = knx_keys_path
        self.knx_keys_pw = knxkeys_pw
        self.knx_gateway = knx_gateway
        self.knx_project: KNXProject|None = None
        self.address_filters: list[AddressFilter] = []
        self.group_addresses: dict[str, GroupAddressInfo] = {}
        if not self.secure_enabled:
            logger.info("KNX IP Secure disabled via config (Secure = false); using non-secure KNX tunneling.")

        # extract knx project with info about devices, group addresses etc.
        if knx_project_path:
            self.knx_project = XKNXProj(path=knx_project_path, password=self.knx_keys_pw).parse()

            # create address filters for rooms based on the assumption that the room name is part of the name of the device or group address
            if rooms_to_monitor:
                logger.debug(f"Rooms to monitor: {rooms_to_monitor}")
                filters: list[AddressFilter] = []
                for room in rooms_to_monitor:
                    filters.extend(list(map(AddressFilter, [k for (k,v) in self.knx_project['devices'].items() if room in v['name'] or room in v['description']])))
                    filters.extend(list(map(AddressFilter, [k for (k,v) in self.knx_project['group_addresses'].items() if room in v['name'] or room in v['description']])))
                if filters:
                    self.address_filters = filters

            # setup group addresses
            for addr, ga in self.knx_project['group_addresses'].items():
                self.group_addresses[addr] = GroupAddressInfo(
                    address=addr,
                    name=ga['name'],
                    description=ga['description'],
                    dpt_main=ga['dpt']['main'] if ga['dpt'] else None, # type: ignore
                    dpt_sub=ga['dpt']['sub'] if ga['dpt'] else None,
                )
        self.__setup_knx_daemon()

    def __setup_knx_daemon(self):
        if self.secure_enabled:
            secure_config = SecureConfig(
                knxkeys_file_path=self.knx_keys_path, 
                knxkeys_password=self.knx_keys_pw)
            
            connection_config = ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP_SECURE, 
                gateway_ip=self.knx_gateway, 
                secure_config=secure_config)
        else:
            connection_config = ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP, 
                gateway_ip=self.knx_gateway)
        
        self.xknx_daemon = XKNX(
            device_updated_cb=self.__device_updated_cb,
            connection_state_changed_cb=self.__connection_state_changed_cb, 
            daemon_mode=True,
            connection_config=connection_config,
        )
        self.xknx_daemon.telegram_queue.register_telegram_received_cb(self.__telegram_received_cb, self.address_filters)            

    def __setup_mqtt(self, mqtt_broker: str|None, mqtt_port: int|None, mqtt_client_id: str|None, mqtt_main_topic: str|None):
        self.mqtt_client: MQTTClient|None = None
        if mqtt_broker and mqtt_port and mqtt_client_id and mqtt_main_topic:
            self.mqtt_client = MQTTClient(
                mqtt_broker,
                mqtt_port,
                mqtt_client_id,
                mqtt_main_topic,
                on_command=self.__handle_command,
            )

    def __telegram_received_cb(self, telegram: Telegram):
        if self.knx_project:
            payload: APCI|None = telegram.payload
            destination = str(telegram.destination_address)
            ga = self.group_addresses.get(destination)
            if not ga:
                logger.warning(f"Skipping telegram for unknown destination address {destination}")
                return
            transcoder = ga.transcoder

            if payload and transcoder:
                value = None
                if payload.CODE in [APCIService.GROUP_WRITE, APCIService.GROUP_RESPONSE]:
                    try:
                        value = transcoder.from_knx(payload.value) # type: ignore
                        value = value.member.name if isinstance(value, enum.EnumType) else value # type: ignore
                    except CouldNotParseTelegram as e:
                        logger.error(f"Could not parse payload {payload.value} for group address {ga.address} (DPT {ga.dpt_main}.{str(ga.dpt_sub).zfill(4)}) - {ga.name}, {ga.description}: {e}") # type: ignore
                elif payload.CODE == APCIService.GROUP_READ:
                    value = None
                else:
                    logger.info(f"Unhandled payload: {payload} - Telegram: {telegram}")
            
                self.publish_message(
                    deviceid=str(telegram.source_address),
                    type=transcoder.value_type,
                    unit=transcoder.unit,
                    value=value,
                    destination=str(telegram.destination_address),
                    direction=telegram.direction.value,
                    device_name=self.knx_project['devices'].get(str(telegram.source_address), {}).get('name', 'Unknown'),
                    destination_name=ga.name,
                    knx_msg_type=payload.CODE,
                )
            else:
                logger.error(f"No transcoder for group address {ga.address} (DPT {ga.dpt_main}.{str(ga.dpt_sub).zfill(4)}) - {ga.name}, {ga.description}")
        logger.debug("Telegram received: {0}".format(telegram))

    def publish_message(self, deviceid, type, unit, value, destination, 
                        direction=None, device_name=None, destination_name=None, knx_msg_type=None):
        """Publish a message to the MQTT broker or write to the console if not running with MQTT."""
        if self.mqtt_client:
            if knx_msg_type in [APCIService.GROUP_WRITE, APCIService.GROUP_RESPONSE, APCIService.GROUP_READ]:
                knx_message_type = (
                    knx_msg_type.name if isinstance(knx_msg_type, enum.Enum) else str(knx_msg_type)
                ) if knx_msg_type is not None else None
                self.mqtt_client.publish(
                    deviceid=deviceid,
                    type=type,
                    unit=unit,
                    value=value,
                    destination=destination,
                    direction=direction,
                    destination_name=destination_name,
                    device_name=device_name,
                    knx_message_type=knx_message_type,
                )
        else:
            # print to console
            print(f"Received message ({knx_msg_type}, {direction}):"
                  f"\n  device: {deviceid} (name: {device_name})"
                  f"\n  type: {type}, unit: {unit}, value: {value}," 
                  f"\n  destination: {destination} (name: {destination_name})")

    def __device_updated_cb(self, device: Device):
        logger.info("Callback received from {0}".format(device.name))

    def __connection_state_changed_cb(self, state: XknxConnectionState):
        logger.info("Callback received with state {0}".format(state.name))

    async def __run_async(self):
        self.loop = asyncio.get_running_loop()
        try:
            await self.xknx_daemon.start()
        except CommunicationError as e:
            logger.error(f"Could not connect to KNX gateway {self.knx_gateway}: {e}")
            exit(1)
        await self.xknx_daemon.stop()

    def run(self):
        if self.mqtt_client:
            self.mqtt_client.run()
        asyncio.run(self.__run_async())
    
    async def stop(self):
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        if self.xknx_daemon:
            await self.xknx_daemon.stop()

    async def read_group_addresses(self, group_addresses: list[str]):
        if not group_addresses:
            return
        for ga in group_addresses:
            try:
                group_value_read(self.xknx_daemon, ga)
                logger.info(f"Sent GroupValueRead for {ga}")
            except Exception as exc:
                logger.error(f"Failed to read group address {ga}: {exc}")

    def __handle_command(self, payload: dict):
        action = payload.get("action") if isinstance(payload, dict) else None
        if action != "read":
            logger.warning(f"Unknown command action: {action}")
            return
        group_addresses = payload.get("destinations") or []
        if not isinstance(group_addresses, list) or not group_addresses:
            logger.warning("Read command missing destinations")
            return
        if not hasattr(self, "loop"):
            logger.warning("Event loop not ready; ignoring read command")
            return
        asyncio.run_coroutine_threadsafe(self.read_group_addresses(group_addresses), self.loop)

@click.command()
@click.option('--config', help='Path to the configuration file.', type=click.Path(exists=True), required=True)
def knx2mqtt(config):
    """A light KNX to MQTT daemon.
    
    All configuration is done via the config file, except for the KNX keys file password, 
    which is passed via the environment variable KNX_KEYS_PW. See exmaple config file
    in the project directory.
    
    If the mqtt broker options are not specified, the KNX daemon will run in a mode that only 
    listens to the KNX bus and prints the telegrams to the console."""

    logging.basicConfig(format="{asctime}: {levelname:<7}: {name:<17}: {message}", style="{", datefmt="%Y-%m-%d %H:%M", force=True)

    # Load configuration file
    config_parser = ConfigParser()
    config_parser.read(config)
    logging.getLogger(knx2mqtt.name).setLevel(config_parser.get("logging", "level").upper())

    # Get password from environment variable
    knx_keys_pw = os.environ.get("KNX_KEYS_PW")
    if not knx_keys_pw:
        logger.error("Please provide KNX password via the KNX_KEYS_PW environment variable.")
        exit(1)

    # disable MQTT via env var?
    skip_mqtt = os.environ.get("KNX2MQTT_SKIP_MQTT", "False").lower() in ["true", "1", "yes"]

    try:
        knx_daemon: KNXDaemon = KNXDaemon(
            knx_gateway = config_parser.get("knx", "Gateway"),
            knx_project_path = config_parser.get("knx", "ProjectPath"),
            knx_keys_path = config_parser.get("knx", "KeysPath", fallback=None),
            knxkeys_pw=knx_keys_pw,
            knx_secure=config_parser.getboolean("knx", "Secure", fallback=True),
            mqtt_broker=config_parser.get("mqtt", "Broker") if not skip_mqtt else None,
            mqtt_port=config_parser.getint("mqtt", "Port"),
            mqtt_client_id=config_parser.get("mqtt", "ClientID"),
            mqtt_main_topic=config_parser.get("mqtt", "MainTopic"),
            rooms_to_monitor=[s.strip() for s in config_parser.get("knx", "MonitorRooms").split(",")]
            )
    except NoOptionError as e:
        logger.error(f"Missing configuration option in config file: {e}")
        exit(1)
    knx_daemon.run()

@click.command()
@click.option('--knx-project', help='The KNX project file.', type=click.Path())
def print_knx_project_json(knx_project):
    """Small utility to print the KNX project file as JSON.
    
    KNX password is passed via the environment variable KNX_KEYS_PW."""

    knxkeys_pw = os.environ.get("KNX_KEYS_PW")

    knx_proj: KNXProject = XKNXProj(path=knx_project, password=knxkeys_pw).parse()
    print(json.dumps(knx_proj, indent=4))
    exit(0)

if __name__ == "__main__":
    knx2mqtt()
