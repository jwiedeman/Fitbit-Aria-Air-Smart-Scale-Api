# Fitbit Aria Air Scale - Self-Hosted API

A self-hosted replacement for Fitbit's cloud API that captures weight and body composition data from your Fitbit Aria (Air) scale. Since Fitbit discontinued their cloud API, this allows you to continue using your scale locally.

## How It Works

The Aria scale connects to WiFi and sends weight data to `www.fitbit.com` via HTTP. By redirecting DNS to point `www.fitbit.com` and `api.fitbit.com` to your Docker host, this server intercepts those requests, parses the binary protocol, and stores your measurements locally.

```
┌─────────────┐         ┌──────────────┐         ┌─────────────────┐
│ Aria Scale  │ ──────▶ │  DNS Redirect │ ──────▶ │  This Server    │
│             │  HTTP   │  (Pi-hole,    │         │  (Docker)       │
│             │         │   router)     │         │                 │
└─────────────┘         └──────────────┘         └─────────────────┘
                                                        │
                                                        ▼
                                                  ┌───────────┐
                                                  │  SQLite   │
                                                  │  Database │
                                                  └───────────┘
```

## Quick Start

### 1. Start the Server

```bash
# Clone the repository
git clone https://github.com/your-repo/Fitbit-Aria-Air-Smart-Scale-Api.git
cd Fitbit-Aria-Air-Smart-Scale-Api

# Start with Docker Compose
docker-compose up -d

# Check logs
docker-compose logs -f
```

### 2. Configure DNS Redirect

You need to redirect `www.fitbit.com` and `api.fitbit.com` to your Docker host's IP address.

#### Option A: Pi-hole (Recommended)

Add local DNS records in Pi-hole:
```
www.fitbit.com    → 192.168.1.100  (your Docker host IP)
api.fitbit.com    → 192.168.1.100
```

#### Option B: Router DNS Override

Many routers support custom DNS entries. Add the same records as above.

#### Option C: Dedicated Network

Create a separate VLAN or network for IoT devices with custom DNS.

#### Option D: /etc/hosts (Testing only)

On Linux/Mac, add to `/etc/hosts`:
```
192.168.1.100  www.fitbit.com
192.168.1.100  api.fitbit.com
```

### 3. Verify It's Working

```bash
# Check the server is running
curl http://localhost:8080/api/health

# Check scale validation endpoint
curl http://localhost:8080/scale/validate
# Should return: T
```

### 4. Step on the Scale

After DNS is configured, step on your Aria scale. You should see logs like:

```
aria-api  | 2024-01-15 10:30:45 - INFO - Received upload: 142 bytes
aria-api  | 2024-01-15 10:30:45 - INFO - Parsed upload from AA:BB:CC:DD:EE:FF: protocol=3, firmware=39, battery=85%, measurements=1
aria-api  | 2024-01-15 10:30:45 - INFO -   Measurement: 75.30kg, fat=18.5%, user=0, time=2024-01-15 10:30:40
```

## API Endpoints

### Scale Endpoints (used by Aria)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/scale/validate` | GET | Returns "T" for validation |
| `/scale/register` | GET | Registers a new scale |
| `/scale/upload` | POST | Receives weight data |

### Management API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/scales` | GET | List registered scales |
| `/api/measurements` | GET | List measurements (paginated) |
| `/api/measurements/latest` | GET | Get most recent measurement |
| `/api/users` | GET | List user profiles |
| `/api/users` | POST | Create user profile |

### Example API Usage

```bash
# Get latest measurement
curl http://localhost:8080/api/measurements/latest

# List all measurements
curl http://localhost:8080/api/measurements?limit=10

# Create a user profile
curl -X POST "http://localhost:8080/api/users?name=John&height_cm=180&age=35&gender=0"

# List registered scales
curl http://localhost:8080/api/scales
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./data/aria.db` | Database connection string |
| `WEIGHT_UNIT` | `kg` | Display unit: `kg`, `lbs`, or `stones` |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### docker-compose.yml Configuration

```yaml
services:
  aria-api:
    environment:
      - WEIGHT_UNIT=lbs      # Change to pounds
      - LOG_LEVEL=DEBUG      # More verbose logging
```

## Data Storage

Data is stored in SQLite at `/app/data/aria.db`. The Docker volume `aria-data` persists this between container restarts.

### Database Tables

- **scales**: Registered scale devices (MAC, firmware, battery)
- **measurements**: Weight/body fat readings with timestamps
- **users**: User profiles synced to the scale
- **raw_uploads**: Raw binary data for debugging

### Export Data

```bash
# Copy database from container
docker cp aria-api:/app/data/aria.db ./aria-backup.db

# Or use SQLite directly
docker exec aria-api sqlite3 /app/data/aria.db ".dump measurements"
```

## Integration Examples

### Home Assistant

Add to `configuration.yaml`:

```yaml
sensor:
  - platform: rest
    name: "Aria Weight"
    resource: http://192.168.1.100:8080/api/measurements/latest
    value_template: "{{ value_json.weight_kg }}"
    unit_of_measurement: "kg"
    scan_interval: 300

  - platform: rest
    name: "Aria Body Fat"
    resource: http://192.168.1.100:8080/api/measurements/latest
    value_template: "{{ value_json.body_fat_percent }}"
    unit_of_measurement: "%"
    scan_interval: 300
```

### Webhook/MQTT (Future)

PRs welcome for MQTT integration. See the reference implementation at [ads04r/aria-spoof](https://github.com/ads04r/aria-spoof) for MQTT examples.

## Troubleshooting

### Scale not connecting

1. Verify DNS redirect is working:
   ```bash
   nslookup www.fitbit.com
   # Should return your Docker host IP
   ```

2. Check the scale is on the same network as DNS server

3. Power cycle the scale (remove batteries, wait 10s, reinsert)

### No data received

1. Check container logs:
   ```bash
   docker-compose logs -f aria-api
   ```

2. Enable debug logging:
   ```yaml
   environment:
     - LOG_LEVEL=DEBUG
   ```

3. Check for catch-all route logs (unknown endpoints)

### CRC errors

The scale uses CRC16-XMODEM checksums. If you see CRC errors, the data may be corrupted in transit. Check network connectivity.

## Protocol Documentation

This implementation is based on reverse-engineering work from:

- [micolous/helvetic](https://github.com/micolous/helvetic) - Django implementation with detailed protocol docs
- [ads04r/aria-spoof](https://github.com/ads04r/aria-spoof) - PHP implementation with MQTT support

### Protocol v3 Format

**Upload Request:**
- Header (30 bytes): protocol version, battery %, MAC, auth code
- Metadata (16 bytes): firmware, timestamp, measurement count
- Measurements (32 bytes each): weight, impedance, body fat, timestamp

**Upload Response:**
- Timestamp, unit preference, status, user profiles
- CRC16-XMODEM checksum
- Trailer bytes (0x66 0x00)

## Development

### Run locally (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create data directory
mkdir -p data

# Run server
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### Run tests

```bash
# TODO: Add tests
pytest
```

## License

MIT License - See LICENSE file.

## Contributing

PRs welcome! Particularly interested in:

- MQTT integration for real-time data
- Prometheus metrics endpoint
- Multi-user support improvements
- Body fat calculation accuracy improvements
- Support for Aria 2 (may use different protocol)

## Disclaimer

This project is not affiliated with Fitbit or Google. Use at your own risk. The Aria scale protocol was reverse-engineered by the community.
