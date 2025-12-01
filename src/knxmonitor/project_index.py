import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from xknxproject.xknxproj import XKNXProj

logger = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if t]


@dataclass
class IndexedItem:
    identifier: str
    name: str
    description: str
    tokens: set[str]

    @property
    def searchable_text(self) -> str:
        return f"{self.name} {self.description}".lower()


@dataclass
class NLResult:
    devices: list[str]
    destinations: list[str]
    explanation: str


class ProjectIndex:
    """Lightweight text matcher over KNX project metadata."""

    def __init__(self, project_path: str | Path, password: str | None = None):
        self.project_path = Path(project_path)
        self.password = password
        self.devices: list[IndexedItem] = []
        self.group_addresses: list[IndexedItem] = []

    def load(self):
        if not self.project_path.exists():
            raise FileNotFoundError(f"KNX project file not found: {self.project_path}")
        logger.info("Loading KNX project for NL matching: %s", self.project_path)
        project = XKNXProj(path=str(self.project_path), password=self.password).parse()
        self.devices = [
            self._index_item(device_id, data.get("name", ""), data.get("description", ""))
            for device_id, data in project.get("devices", {}).items()
        ]
        self.group_addresses = [
            self._index_item(addr, data.get("name", ""), data.get("description", ""))
            for addr, data in project.get("group_addresses", {}).items()
        ]
        logger.info(
            "Indexed %s devices and %s group addresses for NL queries",
            len(self.devices),
            len(self.group_addresses),
        )

    def _index_item(self, identifier: str, name: str, description: str) -> IndexedItem:
        tokens = set(tokenize(f"{identifier} {name} {description}"))
        return IndexedItem(identifier=identifier, name=name, description=description, tokens=tokens)

    def _score_items(self, items: Iterable[IndexedItem], query_tokens: set[str], boost_terms: set[str]) -> list[tuple[int, IndexedItem]]:
        scored: list[tuple[int, IndexedItem]] = []
        for item in items:
            text = item.searchable_text
            score = 0
            for token in query_tokens:
                if token in text:
                    score += 2
                elif token in item.tokens:
                    score += 1
            score += len(boost_terms.intersection(item.tokens))
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def search(self, query: str, limit: int = 100) -> NLResult:
        tokens = set(tokenize(query))
        if not tokens:
            return NLResult(devices=[], destinations=[], explanation="No tokens found in query.")

        # Detect room-like tokens to boost matches (e.g., f200, 252b)
        room_like = {t for t in tokens if re.match(r"^[a-z]?\d{2,4}[a-z]?$", t)}

        device_matches = self._score_items(self.devices, tokens, room_like)[:limit]
        ga_matches = self._score_items(self.group_addresses, tokens, room_like)[:limit]

        devices = [m[1].identifier for m in device_matches]
        destinations = [m[1].identifier for m in ga_matches]

        explanation = "Query: '{0}'. Matched devices: {1}. Destinations: {2}".format(
            query,
            ", ".join(devices) if devices else "none",
            ", ".join(destinations) if destinations else "none",
        )
        return NLResult(devices=devices, destinations=destinations, explanation=explanation)
