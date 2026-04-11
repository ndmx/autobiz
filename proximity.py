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

LOCATION_COORDS.update({
    # Philadelphia metro / collar counties
    "ambler": (40.1546, -75.2216),
    "ardmore": (40.0068, -75.2855),
    "bala cynwyd": (40.0076, -75.2341),
    "bensalem": (40.1046, -74.9513),
    "bristol": (40.1007, -74.8518),
    "bryn mawr": (40.0237, -75.3173),
    "collegeville": (40.1857, -75.4516),
    "devon": (40.0493, -75.4291),
    "downingtown": (40.0065, -75.7033),
    "drexel hill": (39.9471, -75.2921),
    "exton": (40.0326, -75.6275),
    "feasterville": (40.1440, -75.0052),
    "fort washington": (40.1418, -75.2091),
    "glenolden": (39.9001, -75.2891),
    "haverford": (40.0084, -75.3066),
    "havertown": (39.9868, -75.3138),
    "jenkintown": (40.0959, -75.1252),
    "levittown": (40.1551, -74.8288),
    "malvern": (40.0362, -75.5138),
    "manayunk": (40.0265, -75.2230),
    "newtown": (40.2293, -74.9368),
    "paoli": (40.0421, -75.4763),
    "phoenixville": (40.1304, -75.5149),
    "plymouth meeting": (40.1023, -75.2743),
    "pottstown": (40.2454, -75.6496),
    "quakertown": (40.4418, -75.3416),
    "springfield": (39.9307, -75.3202),
    "swarthmore": (39.9021, -75.3499),
    "wayne": (40.0440, -75.3877),
    "willow grove": (40.1440, -75.1157),
    "yardley": (40.2457, -74.8460),
    # Additional PA anchors
    "beaver": (40.6953, -80.3048),
    "bedford": (40.0187, -78.5039),
    "bellefonte": (40.9134, -77.7783),
    "bloomsburg": (41.0037, -76.4549),
    "brookville": (41.1617, -79.0831),
    "butler": (40.8612, -79.8953),
    "carlisle": (40.2015, -77.2003),
    "chambersburg": (39.9376, -77.6611),
    "clearfield": (41.0273, -78.4392),
    "coudersport": (41.7748, -78.0206),
    "danville": (40.9634, -76.6127),
    "ebensburg": (40.4851, -78.7247),
    "franklin": (41.3978, -79.8314),
    "gettysburg": (39.8309, -77.2311),
    "greensburg": (40.3015, -79.5389),
    "honesdale": (41.5768, -75.2588),
    "huntingdon": (40.4848, -78.0103),
    "indiana": (40.6215, -79.1525),
    "kittanning": (40.8165, -79.5217),
    "laporte": (41.4245, -76.4944),
    "lewisburg": (40.9645, -76.8844),
    "lewiston": (40.5992, -77.5714),
    "lewistown": (40.5992, -77.5714),
    "lock haven": (41.1370, -77.4469),
    "mansfield": (41.8073, -77.0775),
    "meadville": (41.6414, -80.1514),
    "mercersburg": (39.8279, -77.9031),
    "mercer": (41.2270, -80.2398),
    "mifflintown": (40.5698, -77.3969),
    "montrose": (41.8339, -75.8774),
    "new bloomfield": (40.4190, -77.1861),
    "new castle": (40.9990, -80.3470),
    "ridgway": (41.4203, -78.7286),
    "selinsgrove": (40.7987, -76.8622),
    "smethport": (41.8090, -78.4447),
    "somerset": (40.0084, -79.0781),
    "stroudsburg": (40.9868, -75.1946),
    "sunbury": (40.8626, -76.7944),
    "tionesta": (41.4953, -79.4556),
    "towanda": (41.7676, -76.4427),
    "tunkhannock": (41.5387, -75.9466),
    "uniontown": (39.9001, -79.7164),
    "warren": (41.8439, -79.1450),
    "waynesburg": (39.8965, -80.1792),
    "wellsboro": (41.7487, -77.3005),
})


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

COUNTY_COORDS.update({
    "adams": LOCATION_COORDS["gettysburg"],
    "armstrong": LOCATION_COORDS["kittanning"],
    "bedford": LOCATION_COORDS["bedford"],
    "bradford": LOCATION_COORDS["towanda"],
    "cameron": (41.5115, -78.2353),
    "clarion": (41.2148, -79.3853),
    "clearfield": LOCATION_COORDS["clearfield"],
    "clinton": LOCATION_COORDS["lock haven"],
    "columbia": LOCATION_COORDS["bloomsburg"],
    "crawford": LOCATION_COORDS["meadville"],
    "cumberland": LOCATION_COORDS["carlisle"],
    "elk": LOCATION_COORDS["ridgway"],
    "fayette": LOCATION_COORDS["uniontown"],
    "forest": LOCATION_COORDS["tionesta"],
    "franklin": LOCATION_COORDS["chambersburg"],
    "fulton": (39.9326, -77.9997),
    "greene": LOCATION_COORDS["waynesburg"],
    "huntingdon": LOCATION_COORDS["huntingdon"],
    "indiana": LOCATION_COORDS["indiana"],
    "jefferson": LOCATION_COORDS["brookville"],
    "juniata": LOCATION_COORDS["mifflintown"],
    "lawrence": LOCATION_COORDS["new castle"],
    "mckean": LOCATION_COORDS["smethport"],
    "mercer": LOCATION_COORDS["mercer"],
    "mifflin": LOCATION_COORDS["lewistown"],
    "montour": LOCATION_COORDS["danville"],
    "northumberland": LOCATION_COORDS["sunbury"],
    "perry": LOCATION_COORDS["new bloomfield"],
    "pike": (41.3259, -74.8024),
    "potter": LOCATION_COORDS["coudersport"],
    "snyder": LOCATION_COORDS["selinsgrove"],
    "somerset": LOCATION_COORDS["somerset"],
    "sullivan": LOCATION_COORDS["laporte"],
    "susquehanna": LOCATION_COORDS["montrose"],
    "tioga": LOCATION_COORDS["wellsboro"],
    "union": LOCATION_COORDS["lewisburg"],
    "venango": LOCATION_COORDS["franklin"],
    "warren": LOCATION_COORDS["warren"],
    "wayne": LOCATION_COORDS["honesdale"],
    "wyoming": LOCATION_COORDS["tunkhannock"],
})


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
