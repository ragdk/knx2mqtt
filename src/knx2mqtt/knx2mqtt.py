import os, sys
import asyncio
import click
import json
import enum
import logging

from .mqtt import MQTTClient

from xknx import XKNX, dpt
from xknx.exceptions import CouldNotParseTelegram
from xknx.telegram import Telegram, AddressFilter
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, APCIService
from xknx.devices import Device
from xknx.core import XknxConnectionState
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknxproject.models import KNXProject
from xknxproject import XKNXProj

# Configure logging
logger = logging.getLogger(__name__)

class GroupAddressInfo:
    """Group address info for runtime usage."""
    address: str
    name: str
    description: str
    dpt_main: int | None
    dpt_sub: int | None
    transcoder: type[dpt.DPTBase] | None

    def __init__(self, address: str, name: str, description: str, dpt_main: int|None, dpt_sub: int|None):
        self.address = address
        self.name = name
        self.description = description
        self.dpt_main = dpt_main
        self.dpt_sub = dpt_main
        self.transcoder = dpt.DPTBase.transcoder_by_dpt(dpt_main, dpt_sub)

class KNXDaemon:
    """Small KNX listening daemon."""

    def __init__(self, gateway: str, knx_project_path: str = None, rooms_to_monitor: list[str] = None, no_mqtt: bool = False):
        # get credentials from environment variables
        self.knxkeys_file_path = os.environ.get("KNX_KEYS_PATH")
        self.knxkeys_pw = os.environ.get("KNX_KEYS_PW")
        self.gateway = gateway
        self.knx_project = None
        self.address_filters: list[AddressFilter] = None
        self.group_addresses: dict[str: GroupAddressInfo] = {}

        # MQTT client
        self.mqtt = not no_mqtt
        if self.mqtt:
            # TODO: add command line arguments or config file for these
            broker = "localhost"  # Replace with your broker address
            port = 1883  # Default MQTT port
            main_topic = "knx"  # Replace with your publish topic
            self.mqtt_client = MQTTClient(broker, port, main_topic)

        # extract knx project with info about devices, group addresses etc.
        if knx_project_path:
            self.knx_project: KNXProject = XKNXProj(path=knx_project_path, password=self.knxkeys_pw).parse()

            # create address filters for rooms based on the assumption that the room name is part of the name of the device or group address
            if rooms_to_monitor:
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
                    dpt_main=ga['dpt']['main'] if ga['dpt'] else None,
                    dpt_sub=ga['dpt']['sub'] if ga['dpt'] else None,
                )
    
    def __telegram_received_cb(self, telegram: Telegram):
        if self.knx_project:
            payload = telegram.payload
            ga = self.group_addresses[str(telegram.destination_address)]
            transcoder = ga.transcoder

            if self.mqtt:
                if not transcoder:
                    logger.error(f"No transcoder for group address {ga.address} (DPT {ga.dpt_main}.{str(ga.dpt_sub).zfill(4)}) - {ga.name}, {ga.description}")
                elif payload.CODE in [APCIService.GROUP_WRITE, APCIService.GROUP_RESPONSE]:
                    try:
                        value = transcoder.from_knx(payload.value)
                        value = value.member.name if isinstance(value, enum.EnumType) else value
                        self.mqtt_client.publish(
                            deviceid=str(telegram.source_address),
                            type=transcoder.value_type,
                            unit=transcoder.unit,
                            value=transcoder.from_knx(payload.value),
                            destination=str(telegram.destination_address)
                        )
                    except CouldNotParseTelegram as e:
                        logger.error(f"Could not parse payload {payload.value} for group address {ga.address} (DPT {ga.dpt_main}.{str(ga.dpt_sub).zfill(4)}) - {ga.name}, {ga.description}: {e}")

                elif payload.CODE == APCIService.GROUP_READ:
                    pass
                else:
                    logger.error(f"Unhandled payload: {payload} - Telegram: {telegram}")
            # this is for debugging purposes
            else: 
                direction = telegram.direction.value
                src = f"{str(telegram.source_address)}, Unknown device"
                if str(telegram.source_address) in self.knx_project['devices'].keys():
                    src = f"{str(telegram.source_address)}, {self.knx_project['devices'][str(telegram.source_address)]['name']}"
                dest = f"{str(telegram.destination_address)}, Unknown group address"
                if str(telegram.destination_address) in self.knx_project['group_addresses'].keys():
                    dest = f"{str(telegram.destination_address)}, {self.knx_project['group_addresses'][str(telegram.destination_address)]['name']}"
                no_payload = False
                msg_type = "Unknown"
                if payload.CODE == APCIService.GROUP_WRITE:
                    msg_type = "Group Write"
                elif payload.CODE == APCIService.GROUP_READ:
                    msg_type = "Group Read"
                    no_payload = True
                elif payload.CODE == APCIService.GROUP_RESPONSE:
                    msg_type = "Group Response"
                else:
                    msg_type = f"Unhandled ({payload.CODE})"
                if transcoder:
                    if no_payload: # read operations has no payload, but we still consider it handling the payload
                        payload = ""
                    else:
                        payload = f" : [type: {transcoder.value_type}, value: {transcoder.from_knx(payload.value)}, unit: {transcoder.unit}]"
                else:
                    logger.error(f"No transcoder for payload: {payload} - Telegram: {telegram}")
                logger.info(f"Msg {direction}, {msg_type}: {src} -> {dest}{payload}")
        else:
            logger.info("Telegram received: {0}".format(telegram))

    def __device_updated_cb(self, device: Device):
        logger.info("Callback received from {0}".format(device.name))

    def __connection_state_changed_cb(self, state: XknxConnectionState):
        logger.info("Callback received with state {0}".format(state.name))

    def __setup_daemon(self):
        if not self.gateway:
            logger.error("ERROR: Please provide KNX/IP gateway IP.")
            exit(1)
        if self.knxkeys_file_path is None or self.knxkeys_pw is None:
            logger.error("ERROR: Please provide KNX keys and password environment variables (KNX_KEYS_PATH and KNX_KEYS_PW)")
            exit(1)

        secure_config = SecureConfig(
            knxkeys_file_path=self.knxkeys_file_path, 
            knxkeys_password=self.knxkeys_pw)
        
        connection_config = ConnectionConfig(
            connection_type=ConnectionType.TUNNELING_TCP_SECURE, 
            gateway_ip=self.gateway, 
            secure_config=secure_config)
        
        self.xknx_daemon = XKNX(
            device_updated_cb=self.__device_updated_cb,
            connection_state_changed_cb=self.__connection_state_changed_cb, 
            daemon_mode=True,
            connection_config=connection_config,
        )
        self.xknx_daemon.telegram_queue.register_telegram_received_cb(self.__telegram_received_cb, self.address_filters)            

    async def __run_async(self):
        await self.xknx_daemon.start()
        await self.xknx_daemon.stop()

    def run(self):
        if self.mqtt:
            self.mqtt_client.connect_and_run()
        self.__setup_daemon()
        asyncio.run(self.__run_async())
    
    def stop(self):
        if self.mqtt:
            self.mqtt_client.disconnect()
        if self.xknx_daemon:
            self.xknx_daemon.stop()

@click.command()
@click.option('--gateway', help='The IP of the KNX/IP gateway', required=False)
@click.option('--knx-project', help='The KNX project file.', type=click.Path(), required=False)
@click.option('--monitor-room', help='The nunmber of a room to monitor.', multiple=True, required=False)
def knx2mqtt(gateway, knx_project, monitor_room):
    logging.basicConfig(format="{asctime}: {levelname:<7}: {name:<17}: {message}", style="{", datefmt="%Y-%m-%d %H:%M", force=True)
    logging.getLogger().setLevel(logging.INFO)

    """Small KNX tool to parse a KNX project file and run a simple KNX listening daemon."""
    if not knx_project and monitor_room:
        logger.error("ERROR: No KNX project file given! - needed to filter on room names.")
        exit(1)

    knx_daemon: KNXDaemon = KNXDaemon(gateway=gateway, knx_project_path=knx_project, rooms_to_monitor=monitor_room)
    knx_daemon.run()

@click.command()
@click.option('--knx-project', help='The KNX project file.', type=click.Path())
def print_knx_project_json(knx_project):
    knxkeys_pw = os.environ.get("KNX_KEYS_PW")

    knx_project: KNXProject = XKNXProj(path=knx_project, password=knxkeys_pw).parse()
    print(json.dumps(knx_project, indent=4))
    exit(0)

if __name__ == "__main__":
    knx2mqtt()