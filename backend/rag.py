"""Deterministic retrieval over the local illustrative destination cost guide."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .config import DESTINATION_GUIDE_PATH
from .models import CostEvidence

_ENTRY_RE = re.compile(r"^([a-z_]+)\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$")

# Per-traveler INR benchmarks used when the guide has no entry for a destination.
# Deliberately mid-market placeholders, not local market data.
GENERIC_ESTIMATES: dict[str, float] = {
    "hotel_budget": 1500.0,
    "hotel_moderate": 3200.0,
    "hotel_luxury": 8500.0,
    "food_budget": 600.0,
    "food_moderate": 1200.0,
    "food_luxury": 2800.0,
    "transport_budget": 600.0,
    "transport_moderate": 900.0,
    "transport_luxury": 1800.0,
    "activities_budget": 500.0,
    "activities_moderate": 1500.0,
    "activities_luxury": 4000.0,
}


class DestinationCostGuide:
    def __init__(self, path: Path | str = DESTINATION_GUIDE_PATH):
        self.path = Path(path)

    def load(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Destination cost guide not found: {self.path}")
        destinations: dict[str, dict[str, object]] = {}
        current: dict[str, object] | None = None
        section_name = ""
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                section_name = line[3:].strip()
                match = re.match(r"(.+?)\s*\|\s*([A-Z]{3})$", section_name)
                if not match:
                    raise ValueError(f"Invalid destination heading: {line}")
                key = match.group(1).strip().casefold()
                current = {
                    "destination": match.group(1).strip(),
                    "currency": match.group(2),
                    "section": section_name,
                    "estimates": {},
                    "assumptions": [],
                }
                destinations[key] = current
                continue
            if current is None or not line or line.startswith("#"):
                continue
            if line.startswith("- "):
                current["assumptions"].append(line[2:].strip())
                continue
            entry = _ENTRY_RE.match(line)
            if entry:
                name, amount = entry.groups()
                current["estimates"][name] = float(amount)
        return destinations

    def destinations(self) -> list[str]:
        return sorted(
            (str(section["destination"]) for section in self.load().values()),
            key=str.casefold,
        )

    def retrieve(
        self,
        destination: str,
        categories: Iterable[str] | None = None,
        fallback: bool = False,
    ) -> CostEvidence | None:
        section = self.load().get(destination.strip().casefold())
        if section is None:
            if fallback:
                return self._generic_evidence(destination, categories)
            return None
        return self._build_evidence(
            section=section,
            destination=str(section["destination"]),
            section_name=str(section["section"]),
            source_file=self.path.name,
            categories=categories,
            is_generic=False,
        )

    def _generic_evidence(
        self,
        destination: str,
        categories: Iterable[str] | None = None,
    ) -> CostEvidence:
        name = destination.strip() or "this destination"
        section = {
            "currency": "INR",
            "estimates": dict(GENERIC_ESTIMATES),
            "assumptions": [
                f"No destination-specific cost-guide entry exists for {name}; "
                "generic illustrative benchmarks in INR are used instead.",
                "Accommodation estimates represent a per-person share of a double room.",
                "Daily category estimates are per traveler per day.",
                "Treat these values as placeholders, not a quote for local market prices.",
            ],
        }
        return self._build_evidence(
            section=section,
            destination=name,
            section_name="Generic benchmark | INR",
            source_file="generic benchmark (not destination-specific)",
            categories=categories,
            is_generic=True,
        )

    def _build_evidence(
        self,
        section: dict[str, object],
        destination: str,
        section_name: str,
        source_file: str,
        categories: Iterable[str] | None,
        is_generic: bool,
    ) -> CostEvidence:
        all_estimates = section["estimates"]
        if categories is None:
            selected = dict(all_estimates)
        else:
            allowed = set(categories)
            selected = {
                key: value for key, value in all_estimates.items()
                if key.rsplit("_", 1)[0] in allowed or key in allowed
            }
        grouped: dict[str, dict[str, float]] = {}
        for key, value in selected.items():
            category, style = key.split("_", 1)
            grouped.setdefault(category, {})[style] = value
        return CostEvidence(
            destination=destination,
            currency=str(section["currency"]),
            section=section_name,
            source_file=source_file,
            estimates=grouped,
            assumptions=list(section["assumptions"]),
            is_generic=is_generic,
        )
