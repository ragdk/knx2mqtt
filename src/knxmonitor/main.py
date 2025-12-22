import asyncio
import logging
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .mqtt_client import MonitorMQTTClient
from .project_index import ProjectIndex, NLResult

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "knxmonitor"
    mqtt_main_topic: str = "knx"
    buffer_size: int = 200
    queue_size: int = 500
    host: str = "0.0.0.0"
    port: int = 8000
    knxproj_path: str | None = None
    knxproj_password: str | None = None


class KnxMessage(BaseModel):
    deviceid: str
    timestamp: str
    destination: str
    message_type: str | None = Field(None, alias="type")
    knx_message_type: str | None = None
    device_name: str | None = None
    unit: str | None = None
    value: Any = None
    direction: str | None = None
    destination_name: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast_json(self, message: dict):
        stale: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


@dataclass
class RuntimeState:
    queue: asyncio.Queue
    buffer: deque
    manager: ConnectionManager
    dispatch_task: asyncio.Task | None = None
    mqtt_client: MonitorMQTTClient | None = None
    project_index: ProjectIndex | None = None


def settings_from_env() -> Settings:
    return Settings(
        mqtt_broker=os.getenv("KNXMONITOR_MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("KNXMONITOR_MQTT_PORT", "1883")),
        mqtt_client_id=os.getenv("KNXMONITOR_MQTT_CLIENT_ID", "knxmonitor"),
        mqtt_main_topic=os.getenv("KNXMONITOR_MQTT_MAIN_TOPIC", "knx"),
        buffer_size=int(os.getenv("KNXMONITOR_BUFFER_SIZE", "200")),
        queue_size=int(os.getenv("KNXMONITOR_QUEUE_SIZE", "500")),
        host=os.getenv("KNXMONITOR_HOST", "0.0.0.0"),
        port=int(os.getenv("KNXMONITOR_PORT", "8000")),
        knxproj_path=os.getenv("KNXMONITOR_KNXPROJ_PATH"),
        knxproj_password=os.getenv("KNXMONITOR_KNXPROJ_PASSWORD") or os.getenv("KNX_KEYS_PW"),
    )


def create_app(settings: Settings) -> FastAPI:
    """Build the FastAPI app and runtime state."""
    app = FastAPI(title="KNX Monitor", version="0.1.0")
    static_path = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_path), name="static")

    runtime = RuntimeState(
        queue=asyncio.Queue(maxsize=settings.queue_size),
        buffer=deque(maxlen=settings.buffer_size),
        manager=ConnectionManager(),
    )
    app.state.settings = settings
    app.state.runtime = runtime

    async def dispatch_loop():
        while True:
            raw_message = await runtime.queue.get()
            try:
                parsed = KnxMessage.model_validate(raw_message)
                payload = parsed.model_dump(by_alias=True)
            except ValidationError as exc:
                logger.warning("Dropping message that did not validate against schema: %s", exc)
                continue
            runtime.buffer.append(payload)
            await runtime.manager.broadcast_json(payload)

    @app.on_event("startup")
    async def startup_event():
        logger.info("Starting knxmonitor with MQTT broker %s:%s", settings.mqtt_broker, settings.mqtt_port)
        runtime.dispatch_task = asyncio.create_task(dispatch_loop())
        loop = asyncio.get_running_loop()
        runtime.mqtt_client = MonitorMQTTClient(
            settings.mqtt_broker,
            settings.mqtt_port,
            settings.mqtt_client_id,
            settings.mqtt_main_topic,
            loop,
            runtime.queue,
        )
        runtime.mqtt_client.start()
        if settings.knxproj_path:
            try:
                runtime.project_index = ProjectIndex(settings.knxproj_path, password=settings.knxproj_password)
                runtime.project_index.load()
            except Exception as exc:
                logger.warning("Could not load KNX project for NL queries: %s", exc)
                runtime.project_index = None

    @app.on_event("shutdown")
    async def shutdown_event():
        if runtime.dispatch_task:
            runtime.dispatch_task.cancel()
            try:
                await runtime.dispatch_task
            except asyncio.CancelledError:
                pass
        if runtime.mqtt_client:
            runtime.mqtt_client.stop()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (static_path / "index.html").read_text(encoding="utf-8")

    @app.get("/api/messages")
    async def get_messages():
        return list(runtime.buffer)

    class NLQuery(BaseModel):
        query: str

    class ReadRequest(BaseModel):
        destinations: list[str]

    @app.post("/api/nl-filter")
    async def nl_filter(body: NLQuery):
        if not runtime.project_index:
            raise HTTPException(status_code=503, detail="KNX project not loaded for NL filtering.")
        result: NLResult = runtime.project_index.search(body.query)
        return {
            "devices": result.devices,
            "destinations": result.destinations,
            "explanation": result.explanation,
        }

    @app.get("/api/ga-index")
    async def ga_index():
        if not runtime.project_index:
            raise HTTPException(status_code=503, detail="KNX project not loaded for group address index.")
        return {"group_addresses": runtime.project_index.group_address_index()}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await runtime.manager.connect(websocket)
        try:
            if runtime.buffer:
                await websocket.send_json({"type": "snapshot", "messages": list(runtime.buffer)})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.manager.disconnect(websocket)
        except Exception:
            runtime.manager.disconnect(websocket)
            raise

    @app.post("/api/read-group-addresses")
    async def read_group_addresses(body: ReadRequest):
        if not runtime.mqtt_client:
            raise HTTPException(status_code=503, detail="MQTT client not ready for KNX reads.")
        destinations = [dest for dest in body.destinations if dest]
        if not destinations:
            raise HTTPException(status_code=400, detail="No destinations provided.")
        try:
            runtime.mqtt_client.request_group_reads(destinations)
        except Exception as exc:
            logger.error("Failed to publish KNX read request: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to publish read request.")
        return {"status": "queued", "count": len(destinations)}

    return app


def configure_logging():
    logging.basicConfig(
        format="{asctime}: {levelname:<7}: {name:<17}: {message}",
        style="{",
        datefmt="%Y-%m-%d %H:%M",
    )
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("knxmonitor").setLevel(logging.INFO)


@click.command()
@click.option("--mqtt-broker", default=None, help="MQTT broker host (overrides KNXMONITOR_MQTT_BROKER).")
@click.option("--mqtt-port", default=None, type=int, help="MQTT broker port.")
@click.option("--mqtt-main-topic", default=None, help="MQTT main topic prefix.")
@click.option("--mqtt-client-id", default=None, help="MQTT client id.")
@click.option("--buffer-size", default=None, type=int, help="Number of recent messages to retain.")
@click.option("--queue-size", default=None, type=int, help="Max queued MQTT messages awaiting broadcast.")
@click.option("--host", default=None, help="Web server host.")
@click.option("--port", default=None, type=int, help="Web server port.")
@click.option("--knxproj-path", default=None, help="Path to KNX project file for NL filtering.")
@click.option("--knxproj-password", default=None, help="Password for KNX project file (if needed).")
def cli(
    mqtt_broker: str | None,
    mqtt_port: int | None,
    mqtt_main_topic: str | None,
    mqtt_client_id: str | None,
    buffer_size: int | None,
    queue_size: int | None,
    host: str | None,
    port: int | None,
    knxproj_path: str | None,
    knxproj_password: str | None,
):
    """Run the KNX monitor web UI."""
    configure_logging()
    base_settings = settings_from_env()
    settings = Settings(
        mqtt_broker=mqtt_broker or base_settings.mqtt_broker,
        mqtt_port=mqtt_port or base_settings.mqtt_port,
        mqtt_client_id=mqtt_client_id or base_settings.mqtt_client_id,
        mqtt_main_topic=mqtt_main_topic or base_settings.mqtt_main_topic,
        buffer_size=buffer_size or base_settings.buffer_size,
        queue_size=queue_size or base_settings.queue_size,
        host=host or base_settings.host,
        port=port or base_settings.port,
        knxproj_path=knxproj_path or base_settings.knxproj_path,
        knxproj_password=knxproj_password or base_settings.knxproj_password,
    )

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, reload=False)


# Default app for `uvicorn knxmonitor.main:app`
app = create_app(settings_from_env())
