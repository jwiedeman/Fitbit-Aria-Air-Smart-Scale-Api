"""
Database models for storing scale data.

Uses SQLAlchemy with PostgreSQL.
"""

import os
import time
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, LargeBinary, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://aria:aria@localhost:5432/aria"
)

# Create engine with connection pool settings appropriate for PostgreSQL
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections before using
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow():
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class Scale(Base):
    """Registered scale device."""
    __tablename__ = "scales"

    id = Column(Integer, primary_key=True, index=True)
    mac_address = Column(String(17), unique=True, index=True)  # AA:BB:CC:DD:EE:FF
    serial_number = Column(String(12), unique=True, index=True)  # MAC as hex
    ssid = Column(String(64), nullable=True)  # WiFi network name
    firmware_version = Column(Integer, nullable=True)
    battery_percent = Column(Integer, nullable=True)
    last_seen = Column(DateTime(timezone=True), default=utcnow)
    registered_at = Column(DateTime(timezone=True), default=utcnow)
    auth_token = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<Scale {self.mac_address}>"


class Measurement(Base):
    """Individual weight measurement."""
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, index=True)
    scale_mac = Column(String(17), index=True)  # Scale MAC address
    measurement_id = Column(Integer)  # ID from scale
    timestamp = Column(DateTime(timezone=True), index=True)  # When measurement was taken
    received_at = Column(DateTime(timezone=True), default=utcnow)  # When we received it

    # Weight data
    weight_grams = Column(Integer)
    weight_kg = Column(Float)
    weight_lbs = Column(Float)

    # Body composition
    impedance = Column(Integer, nullable=True)
    body_fat_percent = Column(Float, nullable=True)
    fat_percent_raw_1 = Column(Integer, nullable=True)
    fat_percent_raw_2 = Column(Integer, nullable=True)
    covariance = Column(Integer, nullable=True)

    # User assignment
    user_id = Column(Integer, default=0)  # 0 = guest
    is_guest = Column(Boolean, default=True)

    def __repr__(self):
        return f"<Measurement {self.weight_kg:.1f}kg @ {self.timestamp}>"


class User(Base):
    """User profile for the scale."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(20))  # Display name on scale (max 20 chars)
    scale_user_id = Column(Integer, unique=True, index=True)  # ID sent to scale
    height_mm = Column(Integer)  # Height in millimeters
    age = Column(Integer)
    gender = Column(Integer, default=0)  # 0 = male, 1 = female
    min_weight_grams = Column(Integer, default=30000)  # ~66 lbs
    max_weight_grams = Column(Integer, default=150000)  # ~330 lbs
    created_at = Column(DateTime(timezone=True), default=utcnow)

    def __repr__(self):
        return f"<User {self.name}>"


class RawUpload(Base):
    """Raw upload data for debugging and replay."""
    __tablename__ = "raw_uploads"

    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime(timezone=True), default=utcnow)
    scale_mac = Column(String(17), nullable=True)
    request_data = Column(LargeBinary)  # Raw binary request
    response_data = Column(LargeBinary, nullable=True)  # Raw binary response
    parsed_ok = Column(Boolean, default=False)
    error_message = Column(String(256), nullable=True)


def wait_for_db(max_retries: int = 30, retry_interval: int = 2):
    """Wait for database to be available."""
    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established")
            return True
        except OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Database not ready (attempt {attempt + 1}/{max_retries}), "
                    f"retrying in {retry_interval}s..."
                )
                time.sleep(retry_interval)
            else:
                logger.error(f"Could not connect to database after {max_retries} attempts: {e}")
                raise


def init_db():
    """Create all database tables."""
    wait_for_db()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
