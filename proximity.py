"""
Philadelphia proximity helpers for PA-wide business listings.

Distances are approximate driving-market heuristics, not geocoded addresses.
They are good enough to rank county/city-level listing cards before deeper
diligence.
"""

from __future__ import annotations

import math
import re
from typing import Optional


PHILADELPHIA = (39.9526, -75.1652)


# Common cities, county seats, and Craigslist market labels that appear in PA
# business listing cards. Coordinates are approximate.
LOCATION_COORDS = {
    "philadelphia": (39.9526, -75.1652),
    "south philadelphia": (39.9290, -75.1690),
    "north philadelphia": (39.9970, -75.1550),
    "downtown philadelphia": (39.9526, -75.1652),
    "center city": (39.9526, -75.1652),
    "lansdowne": (39.9382, -75.2719),
    "upper darby": (39.9284, -75.2816),
    "king of prussia": (40.1013, -75.3836),
    "lansdale": (40.2415, -75.2838),
    "blue bell": (40.1523, -75.2663),
    "conshohocken": (40.0793, -75.3016),
    "norristown": (40.1215, -75.3399),
    "doylestown": (40.3101, -75.1299),
    "media": (39.9168, -75.3877),
    "west chester": (39.9607, -75.6055),
    "chester": (39.8496, -75.3557),
    "wilmington": (39.7391, -75.5398),
    "camden": (39.9259, -75.1196),
    "cherry hill": (39.9348, -75.0307),
    "southjersey": (39.9348, -75.0307),
    "medford": (39.9009, -74.8235),
    "millville": (39.4021, -75.0393),
    "washington": (40.7584, -74.9793),
    "wildwood": (38.9918, -74.8149),
    "allentown": (40.6023, -75.4714),
    "bethlehem": (40.6259, -75.3705),
    "easton": (40.6884, -75.2207),
    "reading": (40.3356, -75.9269),
    "lancaster": (40.0379, -76.3055),
    "york": (39.9626, -76.7277),
    "harrisburg": (40.2732, -76.8867),
    "dover": (40.0018, -76.8503),
    "scranton": (41.4090, -75.6624),
    "wilkes-barre": (41.2459, -75.8813),
    "williamsport": (41.2412, -77.0011),
    "state college": (40.7934, -77.8600),
    "altoona": (40.5187, -78.3947),
    "johnstown": (40.3267, -78.9219),
    "pittsburgh": (40.4406, -79.9959),
    "erie": (42.1292, -80.0851),
}


COUNTY_COORDS = {
    "philadelphia": PHILADELPHIA,
    "bucks": (40.3101, -75.1299),
    "montgomery": (40.1215, -75.3399),
    "delaware": (39.9168, -75.3877),
    "chester": (39.9607, -75.6055),
    "berks": (40.3356, -75.9269),
    "lehigh": (40.6023, -75.4714),
    "northampton": (40.6884, -75.2207),
    "lancaster": (40.0379, -76.3055),
    "york": (39.9626, -76.7277),
    "dauphin": (40.2732, -76.8867),
    "lebanon": (40.3409, -76.4113),
    "schuylkill": (40.6856, -76.1955),
    "carbon": (40.8759, -75.7324),
    "monroe": (40.9868, -75.1946),
    "luzerne": (41.2459, -75.8813),
    "lackawanna": (41.4090, -75.6624),
    "allegheny": (40.4406, -79.9959),
    "washington": (40.1739, -80.2462),
    "westmoreland": (40.3015, -79.5389),
    "beaver": (40.6953, -80.3048),
    "butler": (40.8612, -79.8953),
    "erie": (42.1292, -80.0851),
    "centre": (40.7934, -77.8600),
    "blair": (40.5187, -78.3947),
    "cambria": (40.3267, -78.9219),
    "lycoming": (41.2412, -77.0011),
}


CRAIGSLIST_MARKETS = {
    "philadelphia": PHILADELPHIA,
    "pittsburgh": LOCATION_COORDS["pittsburgh"],
    "allentown": LOCATION_COORDS["allentown"],
    "harrisburg": LOCATION_COORDS["harrisburg"],
    "lancaster": LOCATION_COORDS["lancaster"],
    "reading": LOCATION_COORDS["reading"],
    "scranton": LOCATION_COORDS["scranton"],
    "pennstate": LOCATION_COORDS["state college"],
    "lehighvalley": LOCATION_COORDS["allentown"],
    "poconos": COUNTY_COORDS["monroe"],
    "williamsport": LOCATION_COORDS["williamsport"],
    "york": LOCATION_COORDS["york"],
    "southjersey": LOCATION_COORDS["cherry hill"],
    "delaware": LOCATION_COORDS["wilmington"],
}


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def normalize_location_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9,\s-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_county(location: str) -> str:
    text = normalize_location_text(location)
    match = re.search(r"\b([a-z -]+?)\s+county\b", text)
    if match:
        return match.group(1).strip()
    return ""


def extract_city(location: str) -> str:
    text = normalize_location_text(location)
    if not text or text in {"pa", "pennsylvania"}:
        return ""

    first = text.split(",", 1)[0].strip()
    first = re.sub(r"\bcounty\b", "", first).strip()
    if first in {"pa", "pennsylvania"} or "county" in text:
        return ""
    return first


def coordinates_for_location(location: str, source: str = "") -> Optional[tuple[float, float]]:
    text = normalize_location_text(location)
    source_text = normalize_location_text(source)

    for name, coords in LOCATION_COORDS.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            return coords

    county = extract_county(location)
    if county in COUNTY_COORDS:
        return COUNTY_COORDS[county]

    if text and text not in {"pa", "pennsylvania"} and extract_city(location):
        return None

    for market, coords in CRAIGSLIST_MARKETS.items():
        if market in source_text:
            return coords

    return None


def distance_to_philly(location: str, source: str = "") -> Optional[int]:
    coords = coordinates_for_location(location, source)
    if coords is None:
        return None
    return int(round(haversine_miles(PHILADELPHIA, coords)))


def proximity_bucket(distance: Optional[int]) -> str:
    if distance is None:
        return "unknown distance"
    if distance <= 5:
        return "Philadelphia"
    if distance <= 25:
        return "within 25 mi"
    if distance <= 50:
        return "25-50 mi"
    if distance <= 100:
        return "50-100 mi"
    if distance <= 200:
        return "100-200 mi"
    return "200+ mi"


def add_proximity_fields(item: dict) -> dict:
    location = item.get("location", "")
    source = item.get("_source", "")
    distance = distance_to_philly(location, source)
    item["city"] = item.get("city") or extract_city(location)
    item["county"] = item.get("county") or extract_county(location)
    item["distance_to_philly_miles"] = distance
    item["proximity_bucket"] = proximity_bucket(distance)
    return item


def assign_proximity_ranks(items: list[dict]) -> list[dict]:
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (
            pair[1].get("distance_to_philly_miles") is None,
            pair[1].get("distance_to_philly_miles") or 10_000,
            pair[0],
        ),
    )
    for rank, (_, item) in enumerate(ranked, 1):
        item["proximity_rank"] = rank
    return items
