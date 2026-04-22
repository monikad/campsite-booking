"""
ReserveCalifornia integration for California State Parks.
Uses the Aspira/UseDirect API that powers reservecalifornia.com.
No API key required — these are public endpoints used by the booking website.
"""

import httpx
from datetime import date, timedelta
from typing import Optional

RC_SEARCH_URL = "https://calirdr.usedirect.com/rdr/rdr/search/place"

RC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Origin": "https://www.reservecalifornia.com",
    "Referer": "https://www.reservecalifornia.com/",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_rc_campgrounds(
    latitude: float,
    longitude: float,
    checkin: Optional[date] = None,
    checkout: Optional[date] = None,
    nearby_limit: int = 100,
) -> list[dict]:
    """
    Search ReserveCalifornia for state-park campgrounds near a point.
    Returns normalised campground dicts (same shape as RIDB / NPS results).
    """
    if not checkin:
        checkin = date.today() + timedelta(days=7)
    if not checkout:
        checkout = checkin + timedelta(days=2)

    nights = max((checkout - checkin).days, 1)

    payload = {
        "PlaceId": 0,
        "Latitude": latitude,
        "Longitude": longitude,
        "HighlightedPlaceId": 0,
        "StartDate": checkin.strftime("%m-%d-%Y"),
        "Nights": nights,
        "CountNearby": True,
        "NearbyLimit": nearby_limit,
        "NearbyOnlyAvailable": False,
        "NearbyCountLimit": 10,
        "Sort": "Distance",
        "CustomerId": 0,
        "RefreshFavourites": True,
        "IsADA": False,
        "UnitCategoryId": 0,
        "SleepingUnitId": 0,
        "MinVehicleLength": 0,
        "UnitTypesGroupIds": [],
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(RC_SEARCH_URL, json=payload, headers=RC_HEADERS)
            resp.raise_for_status()
            return _parse_places(resp.json())
    except Exception as e:
        print(f"[ReserveCalifornia] Search error: {e}")
        return []


async def check_rc_availability(
    place_id: int,
    checkin: date,
    checkout: date,
) -> dict:
    """
    Check detailed unit-level availability for a ReserveCalifornia campground.
    """
    nights = max((checkout - checkin).days, 1)

    payload = {
        "PlaceId": place_id,
        "StartDate": checkin.strftime("%m-%d-%Y"),
        "Nights": nights,
        "CountNearby": False,
        "NearbyLimit": 0,
        "NearbyOnlyAvailable": False,
        "NearbyCountLimit": 0,
        "Sort": "Distance",
        "CustomerId": 0,
        "RefreshFavourites": False,
        "IsADA": False,
        "UnitCategoryId": 0,
        "SleepingUnitId": 0,
        "MinVehicleLength": 0,
        "UnitTypesGroupIds": [],
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(RC_SEARCH_URL, json=payload, headers=RC_HEADERS)
            resp.raise_for_status()
            data = resp.json()

        selected = data.get("SelectedPlace") or {}
        facilities = selected.get("Facilities") or {}

        total = 0
        available = 0
        site_details: list[dict] = []

        for _fid, fac in facilities.items():
            for _uid, unit in (fac.get("Units") or {}).items():
                total += 1
                is_avail = bool(unit.get("Available"))
                if is_avail:
                    available += 1
                site_details.append({
                    "site_id": str(unit.get("UnitId", _uid)),
                    "site_name": unit.get("Name", ""),
                    "loop": fac.get("Name", ""),
                    "site_type": unit.get("UnitCategoryName", ""),
                    "max_people": unit.get("MaxOccupancy", 0),
                    "available": is_avail,
                    "available_nights": nights if is_avail else 0,
                    "total_nights_needed": nights,
                })

        site_details.sort(key=lambda s: -int(s["available"]))

        return {
            "facility_id": f"rc_{place_id}",
            "checkin": str(checkin),
            "checkout": str(checkout),
            "available_sites": available,
            "total_sites": total,
            "site_details": site_details,
        }
    except Exception as e:
        print(f"[ReserveCalifornia] Availability error for {place_id}: {e}")
        return {
            "facility_id": f"rc_{place_id}",
            "checkin": str(checkin),
            "checkout": str(checkout),
            "available_sites": 0,
            "total_sites": 0,
            "site_details": [],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_places(data: dict) -> list[dict]:
    """Parse ReserveCalifornia search response into normalised format."""
    results: list[dict] = []

    for place in data.get("NearbyPlaces") or []:
        lat = place.get("Latitude")
        lng = place.get("Longitude")
        if not lat or not lng:
            continue

        pid = place.get("PlaceId", 0)
        name = place.get("Name", "Unknown")
        city = place.get("City", "")
        avail = place.get("Available", False)
        avail_count = place.get("AvailableCount", 0)

        img = place.get("ImageUrl") or ""
        if img and not img.startswith("http"):
            img = f"https://calirdr.usedirect.com{img}"

        desc = place.get("Description") or ""
        if city:
            desc = f"{city}. {desc}" if desc else city

        results.append({
            "id": f"rc_{pid}",
            "name": name,
            "description": desc[:300],
            "lat": float(lat),
            "lng": float(lng),
            "provider": "ReserveCalifornia",
            "reservable": True,
            "type": "State Park Campground",
            "phone": "",
            "email": "",
            "directions": "",
            "image_url": img,
            "images": [img] if img else [],
            "reservation_url": f"https://www.reservecalifornia.com/Web/Default.aspx#!park/{pid}",
            "stay_limit": "",
            "available": avail,
            "available_sites": avail_count if avail else 0,
            "total_sites": None,  # only known after detailed check
            "rc_place_id": pid,
        })

    return results
