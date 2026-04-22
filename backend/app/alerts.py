"""
Alerts engine — seats.aero-style push notifications for campsite openings.

Background scheduler polls availability for active alerts and sends
email (and optionally SMS) when sites open up.
"""
import asyncio
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime
from typing import Optional

from app.database import get_active_alerts, mark_alert_checked, record_notification
from app.availability import check_availability
from app.ridb_client import search_facilities, parse_facilities
from app.reserve_california import search_rc_campgrounds, check_rc_availability


async def check_single_alert(alert: dict) -> list[dict]:
    """
    Check a single alert. Returns list of campgrounds that have openings.
    """
    checkin = date.fromisoformat(alert["checkin"])
    checkout = date.fromisoformat(alert["checkout"])
    
    # Skip alerts for past dates
    if checkout <= date.today():
        return []
    
    notifications = []
    
    if alert.get("facility_id"):
        # Direct facility alert — check just this campground
        fid = alert["facility_id"]
        if fid.startswith("rc_"):
            place_id = int(fid.removeprefix("rc_"))
            result = await check_rc_availability(place_id, checkin, checkout)
        else:
            result = await check_availability(fid, checkin, checkout)
        if result["available_sites"] > 0:
            notifications.append({
                "facility_id": fid,
                "facility_name": alert.get("facility_name", "Unknown Campground"),
                "available_sites": result["available_sites"],
                "total_sites": result["total_sites"],
            })
    elif alert.get("latitude") and alert.get("longitude"):
        # Area-based alert — search nearby campgrounds from all providers
        alat, alng = alert["latitude"], alert["longitude"]

        # RIDB (federal campgrounds)
        try:
            max_miles = min((alert.get("max_drive_hours", 2.0) * 60) * 0.621371, 25)
            raw = await search_facilities(
                latitude=alat,
                longitude=alng,
                radius=max_miles,
            )
            facilities = parse_facilities(raw)

            for facility in facilities[:10]:
                if not facility.get("reservable"):
                    continue
                result = await check_availability(facility["id"], checkin, checkout)
                if result["available_sites"] > 0:
                    notifications.append({
                        "facility_id": facility["id"],
                        "facility_name": facility["name"],
                        "available_sites": result["available_sites"],
                        "total_sites": result["total_sites"],
                    })
        except Exception as e:
            print(f"Alert RIDB search error: {e}")

        # ReserveCalifornia (state parks)
        try:
            rc_places = await search_rc_campgrounds(alat, alng, checkin, checkout)
            for place in rc_places[:5]:
                if place.get("available") and place.get("available_sites", 0) > 0:
                    notifications.append({
                        "facility_id": place["id"],
                        "facility_name": place["name"],
                        "available_sites": place["available_sites"],
                        "total_sites": place.get("total_sites", 0),
                    })
        except Exception as e:
            print(f"Alert RC search error: {e}")
    
    return notifications


def send_email_notification(alert: dict, openings: list[dict]):
    """Send email notification about campsite openings."""
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("ALERT_FROM_EMAIL", smtp_user)
    
    if not smtp_user or not smtp_pass:
        # Log to file so user can verify alerts are working
        log_path = os.path.join(os.path.dirname(__file__), "..", "alert_notifications.log")
        with open(log_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.now().isoformat()}] Alert for {alert['email']}\n")
            f.write(f"Dates: {alert['checkin']} → {alert['checkout']}\n")
            for o in openings:
                url = f"https://www.recreation.gov/camping/campgrounds/{o['facility_id']}"
                f.write(f"  🏕️ {o['facility_name']}: {o['available_sites']}/{o['total_sites']} sites available\n")
                f.write(f"     Book: {url}\n")
        print(f"[ALERT] SMTP not configured — logged {len(openings)} openings to {log_path}")
        print(f"[ALERT]   Would email: {alert['email']}")
        for o in openings:
            print(f"  -> {o['facility_name']}: {o['available_sites']} sites available")
        return
    
    to_email = alert["email"]
    
    # Build email content
    subject = f"🏕️ Campsite Alert: {len(openings)} campground(s) have openings!"
    
    body_lines = [
        f"Great news! We found campsite availability for your dates ({alert['checkin']} to {alert['checkout']}):\n",
    ]
    
    for o in openings:
        url = f"https://www.recreation.gov/camping/campgrounds/{o['facility_id']}"
        body_lines.append(f"🌲 {o['facility_name']}")
        body_lines.append(f"   {o['available_sites']} of {o['total_sites']} sites available")
        body_lines.append(f"   Book now: {url}\n")
    
    body_lines.append("---")
    body_lines.append("You received this because you set up an alert on Campsite Finder.")
    body_lines.append("Reply STOP to unsubscribe.")
    
    body = "\n".join(body_lines)
    
    # HTML version
    html_parts = [
        "<h2>🏕️ Campsite Openings Found!</h2>",
        f"<p>Great news! We found availability for <strong>{alert['checkin']}</strong> to <strong>{alert['checkout']}</strong>:</p>",
        "<ul>",
    ]
    for o in openings:
        url = f"https://www.recreation.gov/camping/campgrounds/{o['facility_id']}"
        html_parts.append(
            f"<li><strong>{o['facility_name']}</strong> — "
            f"{o['available_sites']} of {o['total_sites']} sites available "
            f"<a href='{url}'>Book Now →</a></li>"
        )
    html_parts.append("</ul>")
    html_parts.append("<hr><small>You received this alert from Campsite Finder.</small>")
    html = "\n".join(html_parts)
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html, "html"))
    
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[ALERT] Email sent to {to_email}")
    except Exception as e:
        print(f"[ALERT] Failed to send email to {to_email}: {e}")


async def run_alert_check_cycle():
    """
    Run one full cycle: check all active alerts, send notifications.
    Called by the background scheduler.
    """
    alerts = get_active_alerts()
    print(f"[ALERT] Checking {len(alerts)} active alerts...")
    
    for alert in alerts:
        try:
            openings = await check_single_alert(alert)
            mark_alert_checked(alert["id"])
            
            if openings:
                # Send notification
                send_email_notification(alert, openings)
                
                # Record each notification
                for o in openings:
                    record_notification(
                        alert_id=alert["id"],
                        facility_id=o["facility_id"],
                        facility_name=o["facility_name"],
                        available_sites=o["available_sites"],
                    )
                    
        except Exception as e:
            print(f"[ALERT] Error checking alert {alert['id']}: {e}")
        
        # Brief pause between alerts to respect rate limits
        await asyncio.sleep(1)
    
    print(f"[ALERT] Cycle complete.")
