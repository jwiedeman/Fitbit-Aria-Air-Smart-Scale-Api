# Fitbit Aria Air Scale - Self-Hosted API

A self-hosted replacement for Fitbit's cloud API that captures weight and body composition data from your Fitbit Aria (Air) scale. Since Fitbit discontinued their cloud API, this allows you to continue using your scale locally.

## How It Works

The Aria scale connects to WiFi and sends weight data to `www.fitbit.com` via HTTP. By redirecting DNS to point `www.fitbit.com` and `api.fitbit.com` to your Docker host, this server intercepts those requests, parses the binary protocol, and stores your measurements in PostgreSQL.

```
┌─────────────┐         ┌──────────────┐         ┌─────────────────┐
│ Aria Scale  │ ──────▶ │  DNS Redirect │ ──────▶ │  This Server    │
│             │  HTTP   │  (Pi-hole,    │         │  (Docker)       │
│             │         │   router)     │         │                 │
└─────────────┘         └──────────────┘         └─────────────────┘
                                                        │
                                                        ▼
                                                  ┌───────────┐
                                                  │ PostgreSQL│
                                                  │  :5432    │
                                                  └───────────┘
                                                        │
                                                        ▼
                                                  ┌───────────┐
                                                  │ Your      │
                                                  │ Dashboard │
                                                  └───────────┘
```

## Quick Start

### 1. Start the Server

```bash
# Clone the repository
git clone https://github.com/your-repo/Fitbit-Aria-Air-Smart-Scale-Api.git
cd Fitbit-Aria-Air-Smart-Scale-Api

# Start with Docker Compose (includes PostgreSQL)
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

### 3. Verify It's Working

```bash
# Check the server is running
curl http://localhost/api/health

# Check scale validation endpoint
curl http://localhost/scale/validate
# Should return: T
```

### 4. Step on the Scale

After DNS is configured, step on your Aria scale. You should see logs like:

```
aria-api  | 2024-01-15 10:30:45 - INFO - Received upload: 142 bytes
aria-api  | 2024-01-15 10:30:45 - INFO - Parsed upload from AA:BB:CC:DD:EE:FF: protocol=3, firmware=39, battery=85%, measurements=1
aria-api  | 2024-01-15 10:30:45 - INFO -   Measurement: 75.30kg, fat=18.5%, user=0, time=2024-01-15 10:30:40
```

## Connecting Your Dashboard

PostgreSQL is exposed on port `5432`. Connect with any dashboard tool:

**Connection Details:**
- Host: `localhost` (or your Docker host IP)
- Port: `5432`
- Database: `aria`
- User: `aria`
- Password: `aria`

### Example: Connect with psql

```bash
psql -h localhost -U aria -d aria

# Query recent measurements
SELECT timestamp, weight_kg, body_fat_percent
FROM measurements
ORDER BY timestamp DESC
LIMIT 10;
```

### Example: Grafana Data Source

```yaml
Name: Aria Scale
Type: PostgreSQL
Host: aria-postgres:5432  # or localhost:5432 if external
Database: aria
User: aria
Password: aria
SSL Mode: disable
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
curl http://localhost/api/measurements/latest

# List all measurements
curl "http://localhost/api/measurements?limit=10"

# Create a user profile
curl -X POST "http://localhost/api/users?name=John&height_cm=180&age=35&gender=0"

# List registered scales
curl http://localhost/api/scales
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://aria:aria@postgres:5432/aria` | PostgreSQL connection string |
| `WEIGHT_UNIT` | `kg` | Display unit: `kg`, `lbs`, or `stones` |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### docker-compose.yml Configuration

```yaml
services:
  aria-api:
    environment:
      - WEIGHT_UNIT=lbs      # Change to pounds
      - LOG_LEVEL=DEBUG      # More verbose logging

  postgres:
    environment:
      - POSTGRES_PASSWORD=your_secure_password  # Change in production!
```

## Database Schema

### Tables

**scales** - Registered scale devices
```sql
id, mac_address, serial_number, ssid, firmware_version,
battery_percent, last_seen, registered_at, auth_token, is_active
```

**measurements** - Weight/body fat readings
```sql
id, scale_mac, measurement_id, timestamp, received_at,
weight_grams, weight_kg, weight_lbs,
impedance, body_fat_percent, fat_percent_raw_1, fat_percent_raw_2, covariance,
user_id, is_guest
```

**users** - User profiles synced to scale
```sql
id, name, scale_user_id, height_mm, age, gender,
min_weight_grams, max_weight_grams, created_at
```

**raw_uploads** - Raw binary data for debugging
```sql
id, received_at, scale_mac, request_data, response_data, parsed_ok, error_message
```

### Useful Queries

```sql
-- Daily average weight
SELECT DATE(timestamp) as date, AVG(weight_kg) as avg_weight
FROM measurements
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- Weight trend over last 30 days
SELECT timestamp, weight_kg, body_fat_percent
FROM measurements
WHERE timestamp > NOW() - INTERVAL '30 days'
ORDER BY timestamp;

-- Scale battery status
SELECT mac_address, battery_percent, last_seen
FROM scales
ORDER BY last_seen DESC;
```

## Integration Examples

### Home Assistant

Add to `configuration.yaml`:

```yaml
sensor:
  - platform: rest
    name: "Aria Weight"
    resource: http://192.168.1.100/api/measurements/latest
    value_template: "{{ value_json.weight_kg }}"
    unit_of_measurement: "kg"
    scan_interval: 300

  - platform: rest
    name: "Aria Body Fat"
    resource: http://192.168.1.100/api/measurements/latest
    value_template: "{{ value_json.body_fat_percent }}"
    unit_of_measurement: "%"
    scan_interval: 300
```

### Webhook/MQTT (Future)

PRs welcome for MQTT integration.

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

### Database connection issues

```bash
# Check PostgreSQL is running
docker-compose ps

# Check PostgreSQL logs
docker-compose logs postgres

# Test connection
docker exec -it aria-postgres psql -U aria -d aria -c "SELECT 1"
```

### CRC warnings

The scale uses CRC16-XMODEM checksums. If you see CRC warnings in logs, the data may be slightly corrupted but will still be processed. Different firmware versions may have slight protocol variations.

## Protocol Documentation

This implementation is based on reverse-engineering work from:

- [micolous/helvetic](https://github.com/micolous/helvetic) - Django implementation with detailed protocol docs
- [ads04r/aria-spoof](https://github.com/ads04r/aria-spoof) - PHP implementation with MQTT support

### Protocol v3 Format

**Upload Request:**
- Header (30 bytes): protocol version, battery %, MAC, auth code
- Metadata (16 bytes): firmware, timestamp, measurement count
- Measurements (32 bytes each): weight, impedance, body fat, timestamp
- CRC16-XMODEM checksum (2 bytes)

**Upload Response:**
- Timestamp, unit preference, status, user profiles
- CRC16-XMODEM checksum
- Trailer bytes (0x66 0x00)

## Development

### Run locally (without Docker)

```bash
# Start PostgreSQL (via Docker or local install)
docker run -d --name aria-pg -e POSTGRES_USER=aria -e POSTGRES_PASSWORD=aria -e POSTGRES_DB=aria -p 5432:5432 postgres:16-alpine

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server
DATABASE_URL=postgresql://aria:aria@localhost:5432/aria uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
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
