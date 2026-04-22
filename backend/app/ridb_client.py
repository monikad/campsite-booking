"""
RIDB (Recreation Information Database) API client.
Docs: https://ridb.recreation.gov/docs
Base: https://ridb.recreation.gov/api/v1
Auth: apikey header
Rate: 50 req/sec
"""
import os
import httpx
from typing import Optional

RIDB_BASE = "https://ridb.recreation.gov/api/v1"
# Activity ID 9 = CAMPING in RIDB
CAMPING_ACTIVITY_ID = "9"


def _api_key() -> str:
    key = os.getenv("RIDB_API_KEY", "")
    if not key:
        raise RuntimeError("RIDB_API_KEY env var is not set. Get one at https://ridb.recreation.gov/")
    return key


def _headers() -> dict:
    return {"apikey": _api_key(), "Accept": "application/json"}


async def search_facilities(
    latitude: float,
    longitude: float,
    radius: float = 25,  # miles, max 25
    state: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search RIDB facilities (campgrounds) near a lat/lng."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "radius": min(radius, 25),
        "activity": CAMPING_ACTIVITY_ID,
        "limit": limit,
        "offset": offset,
        "full": "true",
    }
    if state:
        params["state"] = state
    if query:
        params["query"] = query

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{RIDB_BASE}/facilities",
            params=params,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_facility(facility_id: str) -> dict:
    """Get a single facility by ID."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{RIDB_BASE}/facilities/{facility_id}",
            params={"full": "true"},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_facility_campsites(facility_id: str, limit: int = 50, offset: int = 0) -> dict:
    """Get campsites belonging to a facility."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{RIDB_BASE}/facilities/{facility_id}/campsites",
            params={"limit": limit, "offset": offset},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def parse_facilities(raw: dict) -> list[dict]:
    """Normalise RIDB facility records into our app format."""
    results = []
    for f in raw.get("RECDATA", []):
        lat = f.get("FacilityLatitude")
        lng = f.get("FacilityLongitude")
        if not lat or not lng:
            continue

        # Extract image URL if available
        image_url = ""
        media = f.get("ENTITYMEDIA", [])
        if media:
            image_url = media[0].get("URL", "")

        fid = str(f["FacilityID"])
        res_url = f.get("FacilityReservationURL", "") or ""
        # Always provide a link — fall back to the standard recreation.gov URL
        if not res_url:
            res_url = f"https://www.recreation.gov/camping/campgrounds/{fid}"

        results.append({
            "id": fid,
            "name": f.get("FacilityName", "Unknown"),
            "description": (f.get("FacilityDescription") or "")[:300],
            "lat": float(lat),
            "lng": float(lng),
            "provider": "Recreation.gov",
            "reservable": f.get("Reservable", False),
            "type": f.get("FacilityTypeDescription", ""),
            "phone": f.get("FacilityPhone", ""),
            "email": f.get("FacilityEmail", ""),
            "directions": (f.get("FacilityDirections") or "")[:200],
            "image_url": image_url,
            "reservation_url": res_url,
            "stay_limit": f.get("StayLimit", ""),
        })
    return results
