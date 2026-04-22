import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date
from math import radians, cos, sin, asin, sqrt
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from geopy.geocoders import Nominatim

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

from app.database import init_db, create_alert as db_create_alert, get_alerts_by_email, deactivate_alert
from app.ridb_client import search_facilities, parse_facilities
from app.availability import check_availability
from app.alerts import run_alert_check_cycle
from app.nps_client import search_nps_campgrounds
from app.reserve_california import search_rc_campgrounds, check_rc_availability

import time

# ---------------------------------------------------------------------------
# Lifecycle — init DB + background alert scheduler
# ---------------------------------------------------------------------------
alert_task: Optional[asyncio.Task] = None


async def _alert_loop():
    """Background loop that checks alerts every 30 minutes."""
    while True:
        try:
            await run_alert_check_cycle()
        except Exception as e:
            print(f"[ALERT LOOP] Error: {e}")
        await asyncio.sleep(30 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    global alert_task
    alert_task = asyncio.create_task(_alert_loop())
    print("[STARTUP] DB initialised, alert scheduler started (30-min cycle).")
    yield
    if alert_task:
        alert_task.cancel()


app = FastAPI(title="Campsite Booking MVP", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AlertIn(BaseModel):
    email: str
    phone: Optional[str] = None
    facility_id: Optional[str] = None
    facility_name: Optional[str] = None
    reservation_url: Optional[str] = None
    location: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    checkin: str
    checkout: str
    max_drive_hours: Optional[float] = 2.0

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError('Invalid email address')
        return v

    @field_validator('checkin', 'checkout')
    @classmethod
    def validate_dates(cls, v):
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError('Invalid date format, use YYYY-MM-DD')
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371 * c


def km_to_drive_hours(km: float) -> float:
    return km / 80.0


US_STATE_ABBREVS = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


def _extract_rec_gov_id(url: str) -> Optional[str]:
    """Extract recreation.gov campground ID from a URL."""
    if not url:
        return None
    m = re.search(r'recreation\.gov/camping/campgrounds/(\d+)', url)
    return m.group(1) if m else None


def _is_same_campground(a: dict, b: dict) -> bool:
    """Fuzzy-match two campground records by name + proximity."""
    km = haversine(a["lat"], a["lng"], b["lat"], b["lng"])
    if km > 2:
        return False
    name_a = a.get("name", "").lower().strip()
    name_b = b.get("name", "").lower().strip()
    if name_a in name_b or name_b in name_a:
        return True
    noise = {"campground", "campgrounds", "camping", "camp", "the", "a", "at", "of", "in"}
    words_a = set(name_a.split()) - noise
    words_b = set(name_b.split()) - noise
    if words_a and words_b:
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap >= 0.5
    return False


def _merge_results(ridb: list, nps: list, rc: list) -> list[dict]:
    """Merge results from all providers, enriching RIDB with NPS data."""
    merged: dict[str, dict] = {}
    for r in ridb:
        merged[r["id"]] = r
    for n in nps:
        matched = False
        for rid, r in merged.items():
            if _is_same_campground(r, n):
                if n.get("images"):
                    r["images"] = n["images"]
                if n.get("image_url") and not r.get("image_url"):
                    r["image_url"] = n["image_url"]
                for field in ("amenities", "fees", "weather", "park_code"):
                    if n.get(field) and not r.get(field):
                        r[field] = n[field]
                if n.get("description") and len(n["description"]) > len(r.get("description") or ""):
                    r["description"] = n["description"]
                r["nps_enriched"] = True
                matched = True
                break
        if not matched:
            merged[n["id"]] = n
    for rc_item in rc:
        merged[rc_item["id"]] = rc_item
    return list(merged.values())


def geocode_location(location: str):
    """Geocode a location string. Returns {lat, lng, state, state_code} or None."""
    try:
        geolocator = Nominatim(user_agent="campsite-booking-app")
        time.sleep(0.5)
        geo = geolocator.geocode(location, addressdetails=True)
        if geo:
            address = geo.raw.get("address", {})
            state = address.get("state", "")
            return {
                "lat": geo.latitude,
                "lng": geo.longitude,
                "state": state,
                "state_code": US_STATE_ABBREVS.get(state, ""),
            }
        return None
    except Exception as e:
        print(f"Geocoding error: {e}")
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/autocomplete")
def autocomplete(q: str):
    if len(q) < 2:
        return []
    try:
        geolocator = Nominatim(user_agent="campsite-booking-app")
        results = geolocator.geocode(q, exactly_one=False, limit=5, addressdetails=True)
        if not results:
            return []
        suggestions = []
        for result in results:
            address = result.raw.get("address", {})
            city = address.get("city") or address.get("town") or address.get("village", "")
            state = address.get("state", "")
            country = address.get("country", "")
            if country == "United States":
                display = f"{city}, {state}" if city and state else result.address
                suggestions.append({
                    "display": display,
                    "full_address": result.address,
                    "lat": result.latitude,
                    "lng": result.longitude,
                    "state": state,
                    "state_code": US_STATE_ABBREVS.get(state, ""),
                })
        return suggestions[:5]
    except Exception as e:
        print(f"Autocomplete error: {e}")
        return []


@app.get("/search")
async def search(
    location: str,
    max_hours: float = 2.0,
    checkin: Optional[str] = None,
    checkout: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    state: Optional[str] = None,
):
    # --- Resolve coordinates ---
    state_code = state
    if lat is not None and lng is not None:
        pass
    else:
        geo = geocode_location(location)
        if not geo:
            raise HTTPException(status_code=400, detail="Could not find that location.")
        lat, lng = geo["lat"], geo["lng"]
        if not state_code:
            state_code = geo.get("state_code")

    checkin_date = date.fromisoformat(checkin) if checkin else None
    checkout_date = date.fromisoformat(checkout) if checkout else None

    ridb_key = os.getenv("RIDB_API_KEY", "")
    nps_key = os.getenv("NPS_API_KEY", "")

    # --- Run provider searches in parallel ---
    search_coros: dict = {}
    if ridb_key:
        search_coros["ridb"] = _search_ridb(lat, lng, max_hours)
    if nps_key:
        search_coros["nps"] = _search_nps(state_code)
    search_coros["rc"] = _search_rc(lat, lng, checkin_date, checkout_date)

    keys = list(search_coros.keys())
    raw_results = await asyncio.gather(*search_coros.values(), return_exceptions=True)
    provider_results: dict = {}
    for key, result in zip(keys, raw_results):
        if isinstance(result, Exception):
            print(f"[SEARCH] {key} error: {result}")
            provider_results[key] = []
        else:
            provider_results[key] = result

    all_facilities = _merge_results(
        provider_results.get("ridb", []),
        provider_results.get("nps", []),
        provider_results.get("rc", []),
    )

    # --- Filter by distance & check availability ---
    results = []
    for f in all_facilities:
        km = haversine(lat, lng, f["lat"], f["lng"])
        hours = km_to_drive_hours(km)
        if hours > max_hours:
            continue
        f["distance_miles"] = round(km * 0.621371, 1)
        f["drive_hours"] = round(hours, 1)

        if checkin_date and checkout_date and f.get("reservable"):
            if f["provider"] == "ReserveCalifornia":
                pass  # already has availability from search
            else:
                avail_id = f["id"]
                if avail_id.startswith("nps_"):
                    avail_id = _extract_rec_gov_id(f.get("reservation_url", ""))
                if avail_id and not avail_id.startswith("nps_"):
                    f["availability_id"] = avail_id  # rec.gov ID for the expand call
                    try:
                        avail = await check_availability(avail_id, checkin_date, checkout_date)
                        f["available"] = avail["available_sites"] > 0
                        f["available_sites"] = avail["available_sites"]
                        f["total_sites"] = avail["total_sites"]
                    except Exception as e:
                        print(f"Availability error {f['id']}: {e}")
                        f.setdefault("available", None)
                        f.setdefault("available_sites", None)
                        f.setdefault("total_sites", None)
                else:
                    f.setdefault("available", None)
                    f.setdefault("available_sites", None)
                    f.setdefault("total_sites", None)
        else:
            f.setdefault("available", None)
            f.setdefault("available_sites", None)
            f.setdefault("total_sites", None)

        results.append(f)

    results.sort(key=lambda x: x["distance_miles"])

    if not results and not ridb_key and not nps_key:
        results = _mock_search(lat, lng, max_hours)

    return results


# ---------------------------------------------------------------------------
# Provider search helpers
# ---------------------------------------------------------------------------

async def _search_ridb(lat: float, lng: float, max_hours: float) -> list[dict]:
    """Search RIDB with expanding radius strategy."""
    max_miles = max_hours * 50
    seen_ids: set[str] = set()
    facilities: list[dict] = []

    raw = await search_facilities(latitude=lat, longitude=lng, radius=25, limit=50)
    for f in parse_facilities(raw):
        if f["id"] not in seen_ids:
            seen_ids.add(f["id"])
            facilities.append(f)

    if max_miles > 25:
        offset_deg = 0.6
        offset_points = [
            (lat + offset_deg, lng), (lat - offset_deg, lng),
            (lat, lng + offset_deg), (lat, lng - offset_deg),
        ]
        if max_miles > 60:
            d2 = 1.2
            offset_points += [
                (lat + d2, lng), (lat - d2, lng),
                (lat, lng + d2), (lat, lng - d2),
            ]
        for olat, olng in offset_points:
            try:
                raw2 = await search_facilities(latitude=olat, longitude=olng, radius=25, limit=50)
                for f in parse_facilities(raw2):
                    if f["id"] not in seen_ids:
                        seen_ids.add(f["id"])
                        facilities.append(f)
            except Exception:
                pass

    return facilities


async def _search_nps(state_code: Optional[str]) -> list[dict]:
    """Search NPS campgrounds by state."""
    return await search_nps_campgrounds(state_code=state_code)


async def _search_rc(
    lat: float, lng: float,
    checkin: Optional[date], checkout: Optional[date],
) -> list[dict]:
    """Search ReserveCalifornia state park campgrounds."""
    return await search_rc_campgrounds(lat, lng, checkin, checkout)


def _mock_search(lat: float, lng: float, max_hours: float) -> list[dict]:
    """Mock data fallback when no API keys are configured."""
    MOCK = [
        {"id": "232447", "name": "Kirk Creek Campground", "lat": 35.9867, "lng": -121.4969, "provider": "Recreation.gov", "reservable": True, "description": "Stunning Big Sur coastal campground", "image_url": "", "reservation_url": "https://www.recreation.gov/camping/campgrounds/232447"},
        {"id": "233359", "name": "Point Reyes NS Campground", "lat": 38.0505, "lng": -122.8606, "provider": "Recreation.gov", "reservable": True, "description": "Coastal camping at Point Reyes National Seashore", "image_url": "", "reservation_url": "https://www.recreation.gov/camping/campgrounds/233359"},
        {"id": "233683", "name": "Acorn Campground", "lat": 38.0735, "lng": -120.8608, "provider": "Recreation.gov", "reservable": True, "description": "New Melones Lake oak woodland campground", "image_url": "", "reservation_url": "https://www.recreation.gov/camping/campgrounds/233683"},
        {"id": "232308", "name": "Hodgdon Meadow Campground", "lat": 37.7975, "lng": -119.8653, "provider": "Recreation.gov", "reservable": True, "description": "Year-round camping near Yosemite entrance", "image_url": "", "reservation_url": "https://www.recreation.gov/camping/campgrounds/232308"},
        {"id": "232449", "name": "Upper Pines Campground", "lat": 37.7383, "lng": -119.5619, "provider": "Recreation.gov", "reservable": True, "description": "Yosemite Valley's most popular campground", "image_url": "", "reservation_url": "https://www.recreation.gov/camping/campgrounds/232449"},
    ]
    results = []
    for c in MOCK:
        km = haversine(lat, lng, c["lat"], c["lng"])
        hours = km_to_drive_hours(km)
        if hours <= max_hours:
            c["distance_miles"] = round(km * 0.621371, 1)
            c["drive_hours"] = round(hours, 1)
            c["available"] = c["available_sites"] = c["total_sites"] = None
            results.append(c)
    results.sort(key=lambda x: x["distance_miles"])
    return results


@app.get("/availability/{facility_id}")
async def get_availability(facility_id: str, checkin: str, checkout: str):
    try:
        ci = date.fromisoformat(checkin)
        co = date.fromisoformat(checkout)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    if facility_id.startswith("rc_"):
        place_id = int(facility_id.removeprefix("rc_"))
        return await check_rc_availability(place_id, ci, co)
    if facility_id.startswith("nps_"):
        return {"facility_id": facility_id, "checkin": str(ci), "checkout": str(co),
                "available_sites": 0, "total_sites": 0, "site_details": [],
                "note": "Use the reservation link to check NPS campground availability directly."}
    return await check_availability(facility_id, ci, co)


# ---- ALERTS ----

@app.post("/alerts")
async def create_alert_endpoint(payload: AlertIn):
    lat, lng = payload.lat, payload.lng
    if not lat and not lng and payload.location:
        geo = geocode_location(payload.location)
        if geo:
            lat, lng = geo["lat"], geo["lng"]

    alert_id = db_create_alert(
        email=payload.email,
        phone=payload.phone,
        checkin=payload.checkin,
        checkout=payload.checkout,
        facility_id=payload.facility_id,
        facility_name=payload.facility_name,
        reservation_url=payload.reservation_url,
        location_query=payload.location,
        latitude=lat,
        longitude=lng,
        max_drive_hours=payload.max_drive_hours or 2.0,
    )
    return {"id": alert_id, "status": "created", "message": "Alert active! We'll email you when sites open up."}


@app.get("/alerts")
async def list_alerts(email: str):
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    return get_alerts_by_email(email)


@app.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    deactivate_alert(alert_id)
    return {"status": "deactivated"}


@app.post("/alerts/check-now")
async def trigger_alert_check():
    await run_alert_check_cycle()
    return {"status": "check complete"}
