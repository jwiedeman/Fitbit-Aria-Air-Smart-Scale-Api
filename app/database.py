"""
Database models for storing scale data.

Uses SQLAlchemy with SQLite for simplicity and portability.
"""

import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, LargeBinary
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/aria.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Scale(Base):
    """Registered scale device."""
    __tablename__ = "scales"

    id = Column(Integer, primary_key=True, index=True)
    mac_address = Column(String(17), unique=True, index=True)  # AA:BB:CC:DD:EE:FF
    serial_number = Column(String(12), unique=True, index=True)  # MAC as hex
    ssid = Column(String(64), nullable=True)  # WiFi network name
    firmware_version = Column(Integer, nullable=True)
    battery_percent = Column(Integer, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)
    registered_at = Column(DateTime, default=datetime.utcnow)
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
    timestamp = Column(DateTime, index=True)  # When measurement was taken
    received_at = Column(DateTime, default=datetime.utcnow)  # When we received it

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
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.name}>"


class RawUpload(Base):
    """Raw upload data for debugging and replay."""
    __tablename__ = "raw_uploads"

    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime, default=datetime.utcnow)
    scale_mac = Column(String(17), nullable=True)
    request_data = Column(LargeBinary)  # Raw binary request
    response_data = Column(LargeBinary, nullable=True)  # Raw binary response
    parsed_ok = Column(Boolean, default=False)
    error_message = Column(String(256), nullable=True)


def init_db():
    """Create all database tables."""
    # Ensure data directory exists
    if DATABASE_URL.startswith("sqlite:///./"):
        data_dir = os.path.dirname(DATABASE_URL.replace("sqlite:///./", ""))
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)

    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
