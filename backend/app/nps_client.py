"""
National Park Service API client.
Provides richer park info, images, and campground data.

API docs : https://developer.nps.gov/api/v1/
Free key  : https://developer.nps.gov/signup/
"""

import os
import time
import httpx
from typing import Optional

NPS_BASE = "https://developer.nps.gov/api/v1"

# Simple in-memory cache: { key: (timestamp, data) }
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 3600  # 1 hour


def _api_key() -> str:
    key = os.getenv("NPS_API_KEY", "")
    if not key:
        raise RuntimeError("NPS_API_KEY not set. Get one free at https://developer.nps.gov/signup/")
    return key


def _headers() -> dict:
    return {"X-Api-Key": _api_key(), "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_nps_campgrounds(
    state_code: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100,
    start: int = 0,
) -> list[dict]:
    """
    Fetch NPS campgrounds, optionally filtered by state.
    Results are cached per state_code for 1 hour.
    Returns parsed app-format dicts.
    """
    cache_key = f"{state_code or 'all'}:{query or ''}"
    now = time.time()
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    params: dict = {"limit": limit, "start": start}
    if state_code:
        params["stateCode"] = state_code
    if query:
        params["q"] = query

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{NPS_BASE}/campgrounds",
                params=params,
                headers=_headers(),
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        print(f"[NPS] Search error: {e}")
        return []

    parsed = _parse_campgrounds(raw)
    _cache[cache_key] = (now, parsed)
    return parsed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_campgrounds(raw: dict) -> list[dict]:
    """Normalise NPS campground records into our app format."""
    results = []
    for c in raw.get("data", []):
        lat = _float(c.get("latitude"))
        lng = _float(c.get("longitude"))
        if lat is None or lng is None:
            continue

        images = c.get("images", [])
        image_url = images[0].get("url", "") if images else ""

        res_url = c.get("reservationUrl", "") or c.get("url", "") or ""

        reservable = False
        try:
            reservable = int(c.get("numberOfSitesReservable", "0") or "0") > 0
        except (ValueError, TypeError):
            pass

        total_sites = 0
        try:
            total_sites = int(c.get("campsites", {}).get("totalSites", "0") or "0")
        except (ValueError, TypeError):
            pass

        results.append({
            "id": f"nps_{c.get('id', '')}",
            "name": c.get("name", "Unknown"),
            "description": (c.get("description") or "")[:300],
            "lat": lat,
            "lng": lng,
            "provider": "National Park Service",
            "reservable": reservable,
            "type": "Campground",
            "phone": _first_phone(c),
            "email": _first_email(c),
            "directions": (c.get("directionsOverview") or "")[:200],
            "image_url": image_url,
            "images": [img.get("url", "") for img in images[:4]],
            "reservation_url": res_url,
            "stay_limit": "",
            "park_code": c.get("parkCode", ""),
            "total_sites_info": total_sites,
            "amenities": _parse_amenities(c.get("amenities") or {}),
            "weather": (c.get("weatherOverview") or "")[:200],
            "fees": [
                {
                    "title": f.get("title", ""),
                    "cost": f.get("cost", ""),
                    "description": f.get("description", ""),
                }
                for f in c.get("fees", [])
            ],
        })
    return results


def _float(val) -> Optional[float]:
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _first_phone(c: dict) -> str:
    try:
        phones = c.get("contacts", {}).get("phoneNumbers", [])
        return phones[0].get("phoneNumber", "") if phones else ""
    except (IndexError, AttributeError):
        return ""


def _first_email(c: dict) -> str:
    try:
        emails = c.get("contacts", {}).get("emailAddresses", [])
        return emails[0].get("emailAddress", "") if emails else ""
    except (IndexError, AttributeError):
        return ""


def _parse_amenities(amenities: dict) -> list[str]:
    if not amenities:
        return []
    tags: list[str] = []
    mapping = {
        "trashRecyclingCollection": "Trash/Recycling",
        "toilets": "Toilets",
        "showers": "Showers",
        "cellPhoneReception": "Cell Service",
        "campStore": "Camp Store",
        "firewoodForSale": "Firewood",
        "potableWater": "Potable Water",
        "iceAvailableForSale": "Ice",
        "foodStorageLockers": "Food Lockers",
        "staffOrVolunteerHostOnsite": "Host On-Site",
        "amphitheater": "Amphitheater",
        "dumpStation": "Dump Station",
    }
    for key, label in mapping.items():
        val = amenities.get(key)
        if val is None:
            continue
        # Value can be str ("Yes", "No") or list (["Flush Toilets - year round"])
        if isinstance(val, list):
            if any(v and str(v).lower() not in ("none", "no", "") for v in val):
                tags.append(label)
        elif isinstance(val, str):
            if val.lower() not in ("", "no", "0", "none"):
                tags.append(label)
    return tags
