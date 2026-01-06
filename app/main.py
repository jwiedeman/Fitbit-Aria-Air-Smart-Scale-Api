"""
Self-hosted Fitbit Aria Scale API.

This server accepts HTTP requests from Fitbit Aria scales,
parses the weight/body composition data, and stores it locally.

Usage:
    DNS redirect www.fitbit.com and api.fitbit.com to this server.
    The scale will then send all data here instead of Fitbit's cloud.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Depends, Query, Response
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session

from .database import init_db, get_db, Scale, Measurement, User, RawUpload
from .protocol import (
    parse_upload_request,
    build_upload_response,
    build_simple_response,
    WeightUnit,
    UserProfile,
)

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Aria Scale API",
    description="Self-hosted Fitbit Aria Scale API for capturing weight data",
    version="1.0.0",
)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized")


# =============================================================================
# Scale API Endpoints
# =============================================================================

@app.get("/scale/validate")
async def scale_validate():
    """
    Scale validation endpoint.

    The scale calls this to verify the server is responding.
    Must return "T" for the scale to proceed.
    """
    logger.info("Scale validation request received")
    return PlainTextResponse("T")


@app.get("/scale/register")
async def scale_register(
    serialNumber: str = Query(..., description="Scale MAC address as hex"),
    token: str = Query(..., description="Authorization token"),
    ssid: str = Query("", description="WiFi SSID"),
    db: Session = Depends(get_db),
):
    """
    Scale registration endpoint.

    Called when the scale is first set up or reconnects.
    Registers the scale in our database.
    """
    logger.info(f"Scale registration: serial={serialNumber}, ssid={ssid}")

    # Convert serial number to MAC format
    mac_address = ':'.join(
        serialNumber[i:i+2].upper()
        for i in range(0, min(12, len(serialNumber)), 2)
    )

    # Check if scale already exists
    scale = db.query(Scale).filter(Scale.serial_number == serialNumber.upper()).first()

    if scale:
        # Update existing scale
        scale.ssid = ssid
        scale.auth_token = token
        scale.last_seen = datetime.now(timezone.utc)
        logger.info(f"Updated existing scale: {mac_address}")
    else:
        # Create new scale
        scale = Scale(
            mac_address=mac_address,
            serial_number=serialNumber.upper(),
            ssid=ssid,
            auth_token=token,
        )
        db.add(scale)
        logger.info(f"Registered new scale: {mac_address}")

    db.commit()

    # Return success response
    # The scale expects a specific format, but for registration
    # a simple acknowledgment often works
    return PlainTextResponse("OK")


@app.post("/scale/upload")
async def scale_upload(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Scale data upload endpoint.

    This is where the scale sends weight measurements.
    We parse the binary data, store it, and return a valid response.
    """
    # Get raw binary data
    raw_data = await request.body()
    logger.info(f"Received upload: {len(raw_data)} bytes")

    # Store raw data for debugging
    raw_upload = RawUpload(request_data=raw_data)
    db.add(raw_upload)

    try:
        # Parse the upload request
        upload = parse_upload_request(raw_data)

        logger.info(
            f"Parsed upload from {upload.mac_address_str}: "
            f"protocol={upload.protocol_version}, "
            f"firmware={upload.firmware_version}, "
            f"battery={upload.battery_percent}%, "
            f"measurements={len(upload.measurements)}"
        )

        # Update scale record
        scale = db.query(Scale).filter(
            Scale.mac_address == upload.mac_address_str
        ).first()

        if scale:
            scale.firmware_version = upload.firmware_version
            scale.battery_percent = upload.battery_percent
            scale.last_seen = datetime.now(timezone.utc)
        else:
            # Auto-register unknown scale
            scale = Scale(
                mac_address=upload.mac_address_str,
                serial_number=upload.serial_number,
                firmware_version=upload.firmware_version,
                battery_percent=upload.battery_percent,
            )
            db.add(scale)
            logger.info(f"Auto-registered scale: {upload.mac_address_str}")

        # Store each measurement
        for meas in upload.measurements:
            measurement = Measurement(
                scale_mac=upload.mac_address_str,
                measurement_id=meas.measurement_id,
                timestamp=datetime.fromtimestamp(meas.timestamp),
                weight_grams=meas.weight_grams,
                weight_kg=meas.weight_kg,
                weight_lbs=meas.weight_lbs,
                impedance=meas.impedance,
                body_fat_percent=meas.body_fat_percent,
                fat_percent_raw_1=meas.fat_percent_1,
                fat_percent_raw_2=meas.fat_percent_2,
                covariance=meas.covariance,
                user_id=meas.user_id,
                is_guest=meas.is_guest,
            )
            db.add(measurement)

            logger.info(
                f"  Measurement: {meas.weight_kg:.2f}kg, "
                f"fat={meas.body_fat_percent or 'N/A'}%, "
                f"user={meas.user_id}, "
                f"time={datetime.fromtimestamp(meas.timestamp)}"
            )

        # Update raw upload record
        raw_upload.scale_mac = upload.mac_address_str
        raw_upload.parsed_ok = True

        # Build response with user profiles if any
        users = db.query(User).all()
        user_profiles = [
            UserProfile(
                user_id=u.scale_user_id,
                name=u.name,
                min_weight_grams=u.min_weight_grams,
                max_weight_grams=u.max_weight_grams,
                age=u.age,
                gender=u.gender,
                height_mm=u.height_mm,
            )
            for u in users
        ]

        # Get weight unit preference from environment
        unit_str = os.environ.get("WEIGHT_UNIT", "kg").lower()
        unit = {
            "kg": WeightUnit.KILOGRAMS,
            "lbs": WeightUnit.POUNDS,
            "stones": WeightUnit.STONES,
        }.get(unit_str, WeightUnit.KILOGRAMS)

        response_data = build_upload_response(
            unit=unit,
            status=0,
            users=user_profiles,
        )

        raw_upload.response_data = response_data
        db.commit()

        return Response(
            content=response_data,
            media_type="application/octet-stream",
        )

    except Exception as e:
        logger.error(f"Error processing upload: {e}", exc_info=True)
        raw_upload.error_message = str(e)[:256]
        db.commit()

        # Return a simple response even on error
        # This prevents the scale from retrying endlessly
        response_data = build_simple_response()
        return Response(
            content=response_data,
            media_type="application/octet-stream",
        )


# =============================================================================
# Additional Fitbit Endpoints (for compatibility)
# =============================================================================

@app.get("/scale/setup")
async def scale_setup(
    ssid: str = Query("", description="WiFi SSID"),
    custom_password: str = Query("", description="Custom password"),
):
    """
    Scale setup endpoint.

    Called during initial WiFi configuration.
    """
    logger.info(f"Scale setup request: ssid={ssid}")
    return PlainTextResponse("OK")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(request: Request, path: str):
    """
    Catch-all endpoint for unhandled routes.

    Logs the request for debugging and returns OK.
    """
    body = await request.body()
    logger.warning(
        f"Unhandled request: {request.method} /{path} "
        f"({len(body)} bytes body)"
    )
    return PlainTextResponse("OK")


# =============================================================================
# Management API
# =============================================================================

@app.get("/api/scales")
async def list_scales(db: Session = Depends(get_db)):
    """List all registered scales."""
    scales = db.query(Scale).all()
    return [
        {
            "id": s.id,
            "mac_address": s.mac_address,
            "serial_number": s.serial_number,
            "ssid": s.ssid,
            "firmware_version": s.firmware_version,
            "battery_percent": s.battery_percent,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "registered_at": s.registered_at.isoformat() if s.registered_at else None,
        }
        for s in scales
    ]


@app.get("/api/measurements")
async def list_measurements(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List measurements with pagination."""
    measurements = (
        db.query(Measurement)
        .order_by(Measurement.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": m.id,
            "scale_mac": m.scale_mac,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "weight_kg": m.weight_kg,
            "weight_lbs": m.weight_lbs,
            "body_fat_percent": m.body_fat_percent,
            "user_id": m.user_id,
            "is_guest": m.is_guest,
        }
        for m in measurements
    ]


@app.get("/api/measurements/latest")
async def latest_measurement(db: Session = Depends(get_db)):
    """Get the most recent measurement."""
    measurement = (
        db.query(Measurement)
        .order_by(Measurement.timestamp.desc())
        .first()
    )
    if not measurement:
        return JSONResponse(status_code=404, content={"error": "No measurements found"})

    return {
        "id": measurement.id,
        "scale_mac": measurement.scale_mac,
        "timestamp": measurement.timestamp.isoformat() if measurement.timestamp else None,
        "weight_kg": measurement.weight_kg,
        "weight_lbs": measurement.weight_lbs,
        "body_fat_percent": measurement.body_fat_percent,
        "user_id": measurement.user_id,
        "is_guest": measurement.is_guest,
    }


@app.post("/api/users")
async def create_user(
    name: str = Query(..., max_length=20),
    height_cm: int = Query(..., ge=50, le=250),
    age: int = Query(..., ge=1, le=150),
    gender: int = Query(0, ge=0, le=1),
    min_weight_kg: float = Query(30.0),
    max_weight_kg: float = Query(150.0),
    db: Session = Depends(get_db),
):
    """Create a new user profile for the scale."""
    # Get next user ID
    max_user = db.query(User).order_by(User.scale_user_id.desc()).first()
    next_id = (max_user.scale_user_id + 1) if max_user else 1

    user = User(
        name=name[:20],
        scale_user_id=next_id,
        height_mm=height_cm * 10,
        age=age,
        gender=gender,
        min_weight_grams=int(min_weight_kg * 1000),
        max_weight_grams=int(max_weight_kg * 1000),
    )
    db.add(user)
    db.commit()

    return {
        "id": user.id,
        "scale_user_id": user.scale_user_id,
        "name": user.name,
    }


@app.get("/api/users")
async def list_users(db: Session = Depends(get_db)):
    """List all user profiles."""
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "scale_user_id": u.scale_user_id,
            "name": u.name,
            "height_cm": u.height_mm / 10,
            "age": u.age,
            "gender": "male" if u.gender == 0 else "female",
        }
        for u in users
    ]


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "aria-api"}


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Fitbit Aria Scale API (Self-Hosted)",
        "version": "1.0.0",
        "endpoints": {
            "scale": ["/scale/validate", "/scale/register", "/scale/upload"],
            "api": ["/api/scales", "/api/measurements", "/api/users", "/api/health"],
        },
    }
