"""
Fitbit Aria Scale Protocol v3 Parser and Response Generator.

Based on protocol documentation from:
- https://github.com/micolous/helvetic
- https://github.com/ads04r/aria-spoof
"""

import logging
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from .crc import crc16_xmodem, verify_crc, append_crc

logger = logging.getLogger(__name__)


class WeightUnit(IntEnum):
    """Weight display unit preference."""
    POUNDS = 0
    STONES = 1
    KILOGRAMS = 2


@dataclass
class ScaleMeasurement:
    """Individual weight measurement from the scale."""
    measurement_id: int
    impedance: int  # Bio-impedance for body fat calculation
    weight_grams: int
    timestamp: int  # Unix timestamp
    user_id: int  # 0 for guest/unregistered
    fat_percent_1: int  # First body fat reading (scaled by 10)
    fat_percent_2: int  # Second body fat reading (scaled by 10)
    covariance: int  # Body fat covariance

    @property
    def weight_kg(self) -> float:
        """Weight in kilograms."""
        return self.weight_grams / 1000.0

    @property
    def weight_lbs(self) -> float:
        """Weight in pounds."""
        return self.weight_grams / 453.592

    @property
    def body_fat_percent(self) -> Optional[float]:
        """Average body fat percentage, or None if not available."""
        if self.fat_percent_1 == 0 and self.fat_percent_2 == 0:
            return None
        # Average the two readings and divide by 10 (scale factor)
        return (self.fat_percent_1 + self.fat_percent_2) / 20.0

    @property
    def is_guest(self) -> bool:
        """Whether this is a guest measurement."""
        return self.user_id == 0


@dataclass
class UploadRequest:
    """Parsed upload request from the scale."""
    protocol_version: int
    battery_percent: int
    mac_address: bytes
    auth_code: bytes
    firmware_version: int
    scale_timestamp: int
    measurements: list[ScaleMeasurement]
    raw_data: bytes

    @property
    def mac_address_str(self) -> str:
        """MAC address as colon-separated hex string."""
        return ':'.join(f'{b:02X}' for b in self.mac_address)

    @property
    def serial_number(self) -> str:
        """Serial number (MAC as hex without separators)."""
        return self.mac_address.hex().upper()


def parse_upload_request(data: bytes) -> UploadRequest:
    """
    Parse a binary upload request from the scale.

    Protocol v3 format:
    - Header (30 bytes):
      - protocol_version: uint32 (should be 3)
      - battery_percent: uint32
      - mac_address: 6 bytes
      - auth_code: 16 bytes
    - Metadata (16 bytes):
      - firmware_version: uint32
      - unknown: uint32
      - scale_timestamp: uint32
      - measurement_count: uint32
    - Measurements (32 bytes each):
      - measurement_id: uint32
      - impedance: uint32
      - weight_grams: uint32
      - timestamp: uint32
      - user_id: uint32
      - fat_percent_1: uint32
      - fat_percent_2: uint32
      - covariance: uint32
    - CRC16-XMODEM checksum (2 bytes, big-endian)

    Args:
        data: Raw binary data from scale

    Returns:
        Parsed UploadRequest

    Raises:
        ValueError: If data is invalid or checksum fails
    """
    if len(data) < 48:  # Minimum: header + metadata + CRC
        raise ValueError(f"Data too short: {len(data)} bytes")

    # Verify CRC (last 2 bytes)
    if not verify_crc(data):
        # Log warning but continue - some scales may have different CRC behavior
        logger.warning(
            f"CRC verification failed for upload data ({len(data)} bytes). "
            "Proceeding anyway as some firmware versions may use different CRC."
        )

    # Parse header (30 bytes)
    header_fmt = '<LL6s16s'
    header_size = struct.calcsize(header_fmt)
    protocol_version, battery_percent, mac_address, auth_code = struct.unpack(
        header_fmt, data[:header_size]
    )

    if protocol_version != 3:
        raise ValueError(f"Unsupported protocol version: {protocol_version}")

    # Parse metadata (16 bytes)
    meta_fmt = '<LLLL'
    meta_size = struct.calcsize(meta_fmt)
    meta_start = header_size
    firmware_version, unknown, scale_timestamp, measurement_count = struct.unpack(
        meta_fmt, data[meta_start:meta_start + meta_size]
    )

    # Parse measurements (32 bytes each)
    measurement_fmt = '<LLLLLLLL'
    measurement_size = struct.calcsize(measurement_fmt)
    measurements = []

    meas_start = meta_start + meta_size
    for i in range(measurement_count):
        offset = meas_start + (i * measurement_size)
        if offset + measurement_size > len(data) - 2:  # Account for CRC
            break

        meas_data = struct.unpack(
            measurement_fmt, data[offset:offset + measurement_size]
        )
        measurements.append(ScaleMeasurement(
            measurement_id=meas_data[0],
            impedance=meas_data[1],
            weight_grams=meas_data[2],
            timestamp=meas_data[3],
            user_id=meas_data[4],
            fat_percent_1=meas_data[5],
            fat_percent_2=meas_data[6],
            covariance=meas_data[7],
        ))

    return UploadRequest(
        protocol_version=protocol_version,
        battery_percent=battery_percent,
        mac_address=mac_address,
        auth_code=auth_code,
        firmware_version=firmware_version,
        scale_timestamp=scale_timestamp,
        measurements=measurements,
        raw_data=data,
    )


@dataclass
class UserProfile:
    """User profile to send to the scale."""
    user_id: int
    name: str  # Max 20 chars, displayed on scale
    min_weight_grams: int  # Minimum expected weight
    max_weight_grams: int  # Maximum expected weight
    age: int
    gender: int  # 0 = male, 1 = female
    height_mm: int
    last_weight_grams: int = 0
    last_fat_percent: int = 0
    last_timestamp: int = 0


def build_upload_response(
    unit: WeightUnit = WeightUnit.KILOGRAMS,
    status: int = 0,
    users: Optional[list[UserProfile]] = None,
    firmware_update_available: bool = False,
    firmware_url: str = "",
) -> bytes:
    """
    Build a binary response for the scale upload request.

    Response format:
    - timestamp: uint32 (current Unix time)
    - unit: uint32 (0=lbs, 1=stones, 2=kg)
    - status: uint32 (configuration status)
    - user_count: uint32
    - users: array of user profiles (60 bytes each)
    - firmware_update: uint32 (1 if update available)
    - firmware_url: null-terminated string (if update available)
    - trailer: 2 bytes (0x66 0x00 or 0xAC 0x00)
    - CRC16-XMODEM: 2 bytes (big-endian)

    Args:
        unit: Weight display unit
        status: Configuration status (0 = OK)
        users: List of user profiles to send to scale
        firmware_update_available: Whether firmware update is available
        firmware_url: URL for firmware update

    Returns:
        Binary response data
    """
    users = users or []

    # Build response body
    response = struct.pack(
        '<LLLL',
        int(time.time()),  # Current timestamp
        int(unit),  # Weight unit
        status,  # Status
        len(users),  # User count
    )

    # Add user profiles
    for user in users:
        # User profile format: 60 bytes
        # '<L16x20sLLLBxxxLLLLLL'
        name_bytes = user.name.encode('utf-8')[:20].ljust(20, b'\x00')
        user_data = struct.pack(
            '<L',
            user.user_id,
        )
        user_data += b'\x00' * 16  # 16 bytes padding
        user_data += name_bytes  # 20 bytes name
        user_data += struct.pack(
            '<LLLB',
            user.min_weight_grams,
            user.max_weight_grams,
            user.age,
            user.gender,
        )
        user_data += b'\x00' * 3  # 3 bytes padding
        user_data += struct.pack(
            '<LLLLLL',
            user.height_mm,
            user.last_weight_grams,
            user.last_fat_percent,
            user.last_timestamp,
            0,  # Reserved
            0,  # Reserved
        )
        response += user_data

    # Firmware update info
    response += struct.pack('<L', 1 if firmware_update_available else 0)
    if firmware_update_available and firmware_url:
        response += firmware_url.encode('utf-8') + b'\x00'

    # Trailer bytes (0x66 0x00 indicates success)
    response += bytes([0x66, 0x00])

    # Append CRC
    response = append_crc(response)

    return response


def build_simple_response() -> bytes:
    """
    Build a minimal response for the scale.

    This is the simplest valid response that tells the scale
    everything is OK without any user profiles.
    """
    return build_upload_response(
        unit=WeightUnit.KILOGRAMS,
        status=0,
        users=[],
        firmware_update_available=False,
    )
