"""
Self-hosted Fitbit Aria Scale API.

This server accepts HTTP requests from Fitbit Aria scales,
parses the weight/body composition data, and stores it locally.

Usage:
    DNS redirect www.fitbit.com and api.fitbit.com to this server.
    The scale will then send all data here instead of Fitbit's cloud.

Security Note:
    The management API (/api/*) is unauthenticated by default.
    Deploy behind a reverse proxy with auth, or on a trusted network only.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Depends, Query, Response, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session

from sqlalchemy import text as sql_text
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

# Security: Maximum request body size (1MB should be plenty for scale data)
MAX_UPLOAD_SIZE = 1 * 1024 * 1024  # 1MB

# Regex for validating MAC address serial numbers (12 hex chars)
MAC_SERIAL_PATTERN = re.compile(r'^[0-9A-Fa-f]{12}$')

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


def validate_serial_number(serial: str) -> str:
    """
    Validate and normalize a scale serial number (MAC address as hex).

    Args:
        serial: Raw serial number string

    Returns:
        Normalized uppercase serial number

    Raises:
        HTTPException: If serial number is invalid
    """
    serial = serial.strip().upper()
    if not MAC_SERIAL_PATTERN.match(serial):
        logger.warning(f"Invalid serial number format: {serial[:20]}")
        raise HTTPException(
            status_code=400,
            detail="Invalid serial number format. Expected 12 hex characters."
        )
    return serial


def serial_to_mac(serial: str) -> str:
    """Convert 12-char hex serial to MAC address format (AA:BB:CC:DD:EE:FF)."""
    return ':'.join(serial[i:i+2] for i in range(0, 12, 2))


def safe_timestamp_parse(ts: int) -> Optional[datetime]:
    """
    Safely parse a Unix timestamp to datetime.

    Args:
        ts: Unix timestamp

    Returns:
        datetime or None if invalid
    """
    # Sanity check: timestamp should be between 2000 and 2100
    if ts < 946684800 or ts > 4102444800:
        logger.warning(f"Invalid timestamp value: {ts}")
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError, OverflowError) as e:
        logger.warning(f"Failed to parse timestamp {ts}: {e}")
        return None


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
    token: str = Query(..., max_length=64, description="Authorization token"),
    ssid: str = Query("", max_length=64, description="WiFi SSID"),
    db: Session = Depends(get_db),
):
    """
    Scale registration endpoint.

    Called when the scale is first set up or reconnects.
    Registers the scale in our database.
    """
    # Validate and normalize serial number
    serial = validate_serial_number(serialNumber)
    mac_address = serial_to_mac(serial)

    logger.info(f"Scale registration: serial={serial}, ssid={ssid}")

    # Check if scale already exists
    scale = db.query(Scale).filter(Scale.serial_number == serial).first()

    if scale:
        # Update existing scale
        scale.ssid = ssid[:64] if ssid else None
        scale.auth_token = token[:64] if token else None
        scale.last_seen = datetime.now(timezone.utc)
        logger.info(f"Updated existing scale: {mac_address}")
    else:
        # Create new scale
        scale = Scale(
            mac_address=mac_address,
            serial_number=serial,
            ssid=ssid[:64] if ssid else None,
            auth_token=token[:64] if token else None,
        )
        db.add(scale)
        logger.info(f"Registered new scale: {mac_address}")

    db.commit()
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
    # Security: Check content length before reading
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE:
                logger.warning(f"Upload too large: {content_length} bytes")
                raise HTTPException(status_code=413, detail="Request too large")
        except ValueError:
            pass  # Invalid content-length header, let it through

    # Get raw binary data with size limit
    raw_data = await request.body()
    if len(raw_data) > MAX_UPLOAD_SIZE:
        logger.warning(f"Upload body too large: {len(raw_data)} bytes")
        raise HTTPException(status_code=413, detail="Request too large")

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

        # Update raw upload with parsed metadata
        raw_upload.scale_mac = upload.mac_address_str
        raw_upload.protocol_version = upload.protocol_version
        raw_upload.firmware_version = upload.firmware_version
        raw_upload.battery_percent = upload.battery_percent
        raw_upload.scale_timestamp = upload.scale_timestamp
        raw_upload.measurement_count = len(upload.measurements)

        # Update scale record
        scale = db.query(Scale).filter(
            Scale.mac_address == upload.mac_address_str
        ).first()

        if scale:
            scale.firmware_version = upload.firmware_version
            scale.protocol_version = upload.protocol_version
            scale.battery_percent = upload.battery_percent
            scale.auth_code = upload.auth_code.hex() if upload.auth_code else None
            scale.last_seen = datetime.now(timezone.utc)
        else:
            # Auto-register unknown scale
            scale = Scale(
                mac_address=upload.mac_address_str,
                serial_number=upload.serial_number,
                firmware_version=upload.firmware_version,
                protocol_version=upload.protocol_version,
                battery_percent=upload.battery_percent,
                auth_code=upload.auth_code.hex() if upload.auth_code else None,
            )
            db.add(scale)
            logger.info(f"Auto-registered scale: {upload.mac_address_str}")

        # Store each measurement
        for meas in upload.measurements:
            # Safely parse timestamp
            meas_time = safe_timestamp_parse(meas.timestamp)
            if meas_time is None:
                meas_time = datetime.now(timezone.utc)
                logger.warning(f"Using current time for invalid measurement timestamp")

            measurement = Measurement(
                scale_mac=upload.mac_address_str,
                measurement_id=meas.measurement_id,
                timestamp=meas_time,
                timestamp_raw=meas.timestamp,
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
                f"impedance={meas.impedance}, "
                f"fat={meas.body_fat_percent or 'N/A'}%, "
                f"user={meas.user_id}, "
                f"time={meas_time}"
            )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing upload: {e}", exc_info=True)
        raw_upload.error_message = str(e)[:512]
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
    ssid: str = Query("", max_length=64, description="WiFi SSID"),
    custom_password: str = Query("", max_length=64, description="Custom password"),
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
    # Don't read unlimited body for catch-all
    body_preview = b""
    try:
        body = await request.body()
        body_preview = body[:100]  # Only log first 100 bytes
    except Exception:
        pass

    logger.warning(
        f"Unhandled request: {request.method} /{path} "
        f"(body preview: {len(body_preview)} bytes)"
    )
    return PlainTextResponse("OK")


# =============================================================================
# Management API
# Note: These endpoints are unauthenticated. Deploy behind a reverse proxy
# with authentication, or ensure this is only accessible on a trusted network.
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
            "protocol_version": s.protocol_version,
            "battery_percent": s.battery_percent,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "registered_at": s.registered_at.isoformat() if s.registered_at else None,
            "is_active": s.is_active,
        }
        for s in scales
    ]


@app.get("/api/measurements")
async def list_measurements(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    scale_mac: Optional[str] = Query(None, description="Filter by scale MAC"),
    db: Session = Depends(get_db),
):
    """List measurements with pagination and optional filtering."""
    query = db.query(Measurement)

    if user_id is not None:
        query = query.filter(Measurement.user_id == user_id)
    if scale_mac:
        query = query.filter(Measurement.scale_mac == scale_mac.upper())

    measurements = (
        query
        .order_by(Measurement.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": m.id,
            "scale_mac": m.scale_mac,
            "measurement_id": m.measurement_id,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "timestamp_raw": m.timestamp_raw,
            "received_at": m.received_at.isoformat() if m.received_at else None,
            "weight_grams": m.weight_grams,
            "weight_kg": m.weight_kg,
            "weight_lbs": m.weight_lbs,
            "impedance": m.impedance,
            "body_fat_percent": m.body_fat_percent,
            "fat_percent_raw_1": m.fat_percent_raw_1,
            "fat_percent_raw_2": m.fat_percent_raw_2,
            "covariance": m.covariance,
            "user_id": m.user_id,
            "is_guest": m.is_guest,
        }
        for m in measurements
    ]


@app.get("/api/measurements/latest")
async def latest_measurement(
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    db: Session = Depends(get_db),
):
    """Get the most recent measurement."""
    query = db.query(Measurement)
    if user_id is not None:
        query = query.filter(Measurement.user_id == user_id)

    measurement = query.order_by(Measurement.timestamp.desc()).first()

    if not measurement:
        return JSONResponse(status_code=404, content={"error": "No measurements found"})

    return {
        "id": measurement.id,
        "scale_mac": measurement.scale_mac,
        "measurement_id": measurement.measurement_id,
        "timestamp": measurement.timestamp.isoformat() if measurement.timestamp else None,
        "timestamp_raw": measurement.timestamp_raw,
        "received_at": measurement.received_at.isoformat() if measurement.received_at else None,
        "weight_grams": measurement.weight_grams,
        "weight_kg": measurement.weight_kg,
        "weight_lbs": measurement.weight_lbs,
        "impedance": measurement.impedance,
        "body_fat_percent": measurement.body_fat_percent,
        "fat_percent_raw_1": measurement.fat_percent_raw_1,
        "fat_percent_raw_2": measurement.fat_percent_raw_2,
        "covariance": measurement.covariance,
        "user_id": measurement.user_id,
        "is_guest": measurement.is_guest,
    }


@app.post("/api/users")
async def create_user(
    name: str = Query(..., min_length=1, max_length=20),
    height_cm: int = Query(..., ge=50, le=250),
    age: int = Query(..., ge=1, le=150),
    gender: int = Query(0, ge=0, le=1),
    min_weight_kg: float = Query(30.0, ge=10, le=300),
    max_weight_kg: float = Query(150.0, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Create a new user profile for the scale."""
    # Validate weight range
    if min_weight_kg >= max_weight_kg:
        raise HTTPException(
            status_code=400,
            detail="min_weight_kg must be less than max_weight_kg"
        )

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
            "min_weight_kg": u.min_weight_grams / 1000,
            "max_weight_kg": u.max_weight_grams / 1000,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Delete a user profile."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()
    return {"status": "deleted", "id": user_id}


@app.get("/api/raw-uploads")
async def list_raw_uploads(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    errors_only: bool = Query(False, description="Only show failed uploads"),
    db: Session = Depends(get_db),
):
    """List raw upload records for debugging."""
    query = db.query(RawUpload)
    if errors_only:
        query = query.filter(RawUpload.parsed_ok == False)

    uploads = (
        query
        .order_by(RawUpload.received_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": u.id,
            "received_at": u.received_at.isoformat() if u.received_at else None,
            "scale_mac": u.scale_mac,
            "protocol_version": u.protocol_version,
            "firmware_version": u.firmware_version,
            "battery_percent": u.battery_percent,
            "scale_timestamp": u.scale_timestamp,
            "measurement_count": u.measurement_count,
            "request_size": len(u.request_data) if u.request_data else 0,
            "response_size": len(u.response_data) if u.response_data else 0,
            "parsed_ok": u.parsed_ok,
            "error_message": u.error_message,
        }
        for u in uploads
    ]


@app.get("/api/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint with database connectivity test."""
    try:
        # Test database connection
        db.execute(sql_text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:100]}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "aria-api",
        "database": db_status,
    }


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Fitbit Aria Scale API (Self-Hosted)",
        "version": "1.0.0",
        "endpoints": {
            "scale": ["/scale/validate", "/scale/register", "/scale/upload"],
            "api": [
                "/api/scales",
                "/api/measurements",
                "/api/measurements/latest",
                "/api/users",
                "/api/raw-uploads",
                "/api/health",
            ],
        },
        "security_note": "Management API is unauthenticated. Deploy on trusted network only.",
    }
