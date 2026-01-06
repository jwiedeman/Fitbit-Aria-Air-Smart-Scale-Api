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
aria-api  | 2024-01-15 10:30:45 - INFO -   Measurement: 75.30kg, impedance=520, fat=18.5%, user=0, time=2024-01-15 10:30:40
```

## Security Considerations

**The management API (`/api/*`) is UNAUTHENTICATED by default.**

### Recommendations:

1. **Network isolation**: Only deploy on a trusted network (home LAN, IoT VLAN)
2. **Reverse proxy**: Use nginx/Traefik with basic auth for the `/api/*` endpoints
3. **Change default password**: Edit `docker-compose.yml` to change the PostgreSQL password
4. **Restrict PostgreSQL access**: Remove or firewall port 5432 if external access not needed

### Example: Nginx with Basic Auth

```nginx
location /api/ {
    auth_basic "Aria API";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://aria-api:80;
}

location /scale/ {
    # No auth for scale endpoints (scale can't authenticate)
    proxy_pass http://aria-api:80;
}
```

## Connecting Your Dashboard

PostgreSQL is exposed on port `5432`. Connect with any dashboard tool:

**Connection Details:**
- Host: `localhost` (or your Docker host IP)
- Port: `5432`
- Database: `aria`
- User: `aria`
- Password: `aria` (change this!)

### Example: Connect with psql

```bash
psql -h localhost -U aria -d aria

# Query recent measurements
SELECT timestamp, weight_kg, body_fat_percent, impedance
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

## Data Captured

All available data from the scale is stored:

### Measurements Table

| Field | Type | Description |
|-------|------|-------------|
| `weight_grams` | int | Weight in grams |
| `weight_kg` | float | Weight in kilograms |
| `weight_lbs` | float | Weight in pounds |
| `impedance` | int | Bio-electrical impedance (ohms) |
| `body_fat_percent` | float | Calculated body fat percentage |
| `fat_percent_raw_1` | int | First raw reading (x10) |
| `fat_percent_raw_2` | int | Second raw reading (x10) |
| `covariance` | int | Measurement quality/covariance |
| `timestamp` | datetime | When measurement was taken |
| `timestamp_raw` | bigint | Raw Unix timestamp from scale |
| `received_at` | datetime | When server received it |
| `user_id` | int | User ID (0 = guest) |
| `is_guest` | bool | Whether this is a guest measurement |

### Scales Table

| Field | Type | Description |
|-------|------|-------------|
| `mac_address` | string | Scale MAC address |
| `serial_number` | string | Serial number (MAC as hex) |
| `firmware_version` | int | Firmware version |
| `protocol_version` | int | Protocol version (usually 3) |
| `battery_percent` | int | Battery level |
| `ssid` | string | WiFi network name |
| `auth_code` | string | 16-byte auth code (hex) |

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
| `/api/health` | GET | Health check with DB status |
| `/api/scales` | GET | List registered scales |
| `/api/measurements` | GET | List measurements (paginated) |
| `/api/measurements/latest` | GET | Get most recent measurement |
| `/api/users` | GET/POST | List/create user profiles |
| `/api/users/{id}` | DELETE | Delete a user profile |
| `/api/raw-uploads` | GET | List raw upload data (debugging) |

### Example API Usage

```bash
# Get latest measurement
curl http://localhost/api/measurements/latest

# Get latest for specific user
curl "http://localhost/api/measurements/latest?user_id=1"

# List measurements with all data
curl "http://localhost/api/measurements?limit=10"

# Filter by scale
curl "http://localhost/api/measurements?scale_mac=AA:BB:CC:DD:EE:FF"

# Create a user profile
curl -X POST "http://localhost/api/users?name=John&height_cm=180&age=35&gender=0"

# Delete a user
curl -X DELETE http://localhost/api/users/1

# Check for failed uploads
curl "http://localhost/api/raw-uploads?errors_only=true"
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://aria:aria@postgres:5432/aria` | PostgreSQL connection string |
| `WEIGHT_UNIT` | `kg` | Display unit: `kg`, `lbs`, or `stones` |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Database Schema

### Useful Queries

```sql
-- Daily average weight
SELECT DATE(timestamp) as date,
       AVG(weight_kg) as avg_weight,
       AVG(body_fat_percent) as avg_fat
FROM measurements
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- Weight trend over last 30 days
SELECT timestamp, weight_kg, body_fat_percent, impedance
FROM measurements
WHERE timestamp > NOW() - INTERVAL '30 days'
ORDER BY timestamp;

-- Scale battery status
SELECT mac_address, battery_percent, firmware_version, last_seen
FROM scales
ORDER BY last_seen DESC;

-- Measurements with impedance data (for body composition analysis)
SELECT timestamp, weight_kg, impedance, body_fat_percent,
       fat_percent_raw_1, fat_percent_raw_2, covariance
FROM measurements
WHERE impedance IS NOT NULL
ORDER BY timestamp DESC;
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

  - platform: rest
    name: "Aria Impedance"
    resource: http://192.168.1.100/api/measurements/latest
    value_template: "{{ value_json.impedance }}"
    unit_of_measurement: "Ω"
    scan_interval: 300
```

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

### Check failed uploads

```bash
curl "http://localhost/api/raw-uploads?errors_only=true"
```

## Protocol Documentation

This implementation is based on reverse-engineering work from:

- [micolous/helvetic](https://github.com/micolous/helvetic) - Django implementation with detailed protocol docs
- [ads04r/aria-spoof](https://github.com/ads04r/aria-spoof) - PHP implementation with MQTT support

### Protocol v3 Format

**Upload Request:**
- Header (30 bytes): protocol version, battery %, MAC address, 16-byte auth code
- Metadata (16 bytes): firmware version, scale timestamp, measurement count
- Measurements (32 bytes each): ID, impedance, weight, timestamp, user ID, body fat readings, covariance
- CRC16-XMODEM checksum (2 bytes)

**Upload Response:**
- Timestamp, unit preference, status, user profiles
- CRC16-XMODEM checksum
- Trailer bytes (0x66 0x00)

## Development

### Run locally (without Docker)

```bash
# Start PostgreSQL (via Docker or local install)
docker run -d --name aria-pg \
  -e POSTGRES_USER=aria \
  -e POSTGRES_PASSWORD=aria \
  -e POSTGRES_DB=aria \
  -p 5432:5432 \
  postgres:16-alpine

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server
DATABASE_URL=postgresql://aria:aria@localhost:5432/aria \
  uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
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
- Authentication middleware for management API

## Disclaimer

This project is not affiliated with Fitbit or Google. Use at your own risk. The Aria scale protocol was reverse-engineered by the community.
