"""
Recreation.gov campground availability checker.
Uses the public availability API at recreation.gov.
"""
import httpx
from datetime import date, datetime, timedelta
from typing import Optional


AVAILABILITY_BASE = "https://www.recreation.gov/api/camps/availability/campground"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) campsite-booking-app",
    "Accept": "application/json",
}


async def check_availability(
    facility_id: str,
    checkin: date,
    checkout: date,
) -> dict:
    """
    Check availability for a campground over a date range.
    Returns { "available_sites": int, "total_sites": int, "site_details": [...] }
    """
    # We need to check each month that overlaps the date range
    months_to_check = _get_months(checkin, checkout)
    
    all_sites: dict[str, dict] = {}  # site_id -> availability info
    
    async with httpx.AsyncClient(timeout=15) as client:
        for month_start in months_to_check:
            url = f"{AVAILABILITY_BASE}/{facility_id}/month"
            params = {
                "start_date": f"{month_start.strftime('%Y-%m-01')}T00:00:00.000Z"
            }
            try:
                resp = await client.get(url, params=params, headers=HEADERS)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                
                for site_id, site_data in data.get("campsites", {}).items():
                    if site_id not in all_sites:
                        all_sites[site_id] = {
                            "site_id": site_id,
                            "site_name": site_data.get("site", ""),
                            "loop": site_data.get("loop", ""),
                            "site_type": site_data.get("campsite_type", ""),
                            "max_people": site_data.get("max_num_people", 0),
                            "available_dates": [],
                            "unavailable_dates": [],
                        }
                    
                    # Check each date in the availabilities dict
                    for date_str, status in site_data.get("availabilities", {}).items():
                        try:
                            d = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
                        except (ValueError, TypeError):
                            continue
                        
                        if checkin <= d < checkout:
                            if status == "Available":
                                all_sites[site_id]["available_dates"].append(str(d))
                            else:
                                all_sites[site_id]["unavailable_dates"].append(str(d))
            except Exception as e:
                print(f"Availability check error for {facility_id}, month {month_start}: {e}")
                continue
    
    # A site is fully available if ALL nights in the range are available
    num_nights = (checkout - checkin).days
    available_count = 0
    site_details = []
    
    for site_id, info in all_sites.items():
        is_available = len(info["available_dates"]) >= num_nights
        detail = {
            "site_id": info["site_id"],
            "site_name": info["site_name"],
            "loop": info["loop"],
            "site_type": info["site_type"],
            "max_people": info["max_people"],
            "available": is_available,
            "available_nights": len(info["available_dates"]),
            "total_nights_needed": num_nights,
        }
        site_details.append(detail)
        if is_available:
            available_count += 1
    
    return {
        "facility_id": facility_id,
        "checkin": str(checkin),
        "checkout": str(checkout),
        "available_sites": available_count,
        "total_sites": len(all_sites),
        "site_details": sorted(site_details, key=lambda x: -int(x["available"])),
    }


def _get_months(checkin: date, checkout: date) -> list[date]:
    """Get list of first-of-month dates that cover the checkin-checkout range."""
    months = []
    current = checkin.replace(day=1)
    end = checkout.replace(day=1)
    while current <= end:
        months.append(current)
        # Advance to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months
