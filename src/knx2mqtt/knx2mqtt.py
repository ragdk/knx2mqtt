import os, sys
import asyncio
import click
import json

from xknx import XKNX, dpt
from xknx.telegram import Telegram, AddressFilter
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, APCIService
from xknx.devices import Device
from xknx.core import XknxConnectionState
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknxproject.models import KNXProject
from xknxproject import XKNXProj

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
        if dpt_main == 1 and not dpt_sub:
            self.transcoder = dpt.DPTBase.transcoder_by_dpt(dpt_main, 2)
        else: 
            self.transcoder = dpt.DPTBase.transcoder_by_dpt(dpt_main, dpt_sub) if dpt_main else None
        if not self.transcoder:
            print(f'No transcoder found for group address {address} (DPT {dpt_main}.{str(dpt_sub).zfill(4)}) - {name}, {description}', file=sys.stderr)

class KNXDaemon:
    """Small KNX listening daemon."""

    def __init__(self, gateway: str, knx_project_path: str = None, rooms_to_monitor: list[str] = None, ):
        # get credentials from environment variables
        self.knxkeys_file_path = os.environ.get("KNX_KEYS_PATH")
        self.knxkeys_pw = os.environ.get("KNX_KEYS_PW")
        self.gateway = gateway
        self.knx_project = None
        self.address_filters: list[AddressFilter] = None
        self.group_addresses: dict[str: GroupAddressInfo] = {}

        # extract knx project with info about devices, group addresses etc.
        if knx_project_path:
            self.knx_project: KNXProject = XKNXProj(path=knx_project_path, password=self.knxkeys_pw).parse()

            # create address filters for rooms
            if rooms_to_monitor:
                for room in rooms_to_monitor:
                    self.address_filters.extend(list(map(AddressFilter, [k for (k,v) in self.knx_project['devices'].items() if room in v['name'] or room in v['description']])))
                    self.address_filters.extend(list(map(AddressFilter, [k for (k,v) in self.knx_project['group_addresses'].items() if room in v['name'] or room in v['description']])))

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
            direction = telegram.direction.value
            src = f"{str(telegram.source_address)}, Unknown device"
            if str(telegram.source_address) in self.knx_project['devices'].keys():
                src = f"{str(telegram.source_address)}, {self.knx_project['devices'][str(telegram.source_address)]['name']}"
            else:
                print(f"{src} - Telegram: {telegram}", file=sys.stderr)
            dest = f"{str(telegram.destination_address)}, Unknown group address"
            if str(telegram.destination_address) in self.knx_project['group_addresses'].keys():
                dest = f"{str(telegram.destination_address)}, {self.knx_project['group_addresses'][str(telegram.destination_address)]['name']}"
            else:
                print(f"{dest} - Telegram: {telegram}", file=sys.stderr)
            transcoder = self.group_addresses[str(telegram.destination_address)].transcoder
            payload = telegram.payload
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
                print(f"No transcoder for payload: {payload} - Telegram: {telegram}", file=sys.stderr)
            print(f"Msg {direction}, {msg_type}: {src} -> {dest}{payload}")
        else:
            print("Telegram received: {0}".format(telegram))

    def __device_updated_cb(self, device: Device):
        print("Callback received from {0}".format(device.name))

    def __connection_state_changed_cb(self, state: XknxConnectionState):
        print("Callback received with state {0}".format(state.name))

    def __setup_daemon(self):
        if not self.gateway:
            exit("ERROR: Please provide KNX/IP gateway IP.")
        if self.knxkeys_file_path is None or self.knxkeys_pw is None:
            exit("ERROR: Please provide KNX keys and password environment variables (KNX_KEYS_PATH and KNX_KEYS_PW)")

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
        self.__setup_daemon()
        asyncio.run(self.__run_async())
    
    def project_as_json(self) -> dict:
        return json.dumps(self.knx_project, indent=4)

@click.command()
@click.option('--gateway', help='The IP of the KNX/IP gateway', required=False)
@click.option('--knx-project', help='The KNX project file.', type=click.Path(), required=False)
@click.option('--monitor-room', help='The nunmber of a room to monitor.', multiple=True, required=False)
@click.option('--print-project-json', help='Print project JSON to stdout and skip running the listener.', is_flag=True)
def knx2mqtt(gateway, knx_project, monitor_room, print_project_json):
    """Small KNX tool to parse a KNX project file and run a simple KNX listening deamon."""
    if not knx_project and (print_project_json or monitor_room):
        exit("ERROR: No KNX project file given!")

    knx_daemon: KNXDaemon = KNXDaemon(gateway=gateway, knx_project_path=knx_project, rooms_to_monitor=monitor_room)

    if print_project_json:
        print(knx_daemon.project_as_json())
        exit(0)

    knx_daemon.run()

if __name__ == "__main__":
    knx2mqtt()
