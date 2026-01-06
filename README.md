# 🏋️ Fitbit Aria Scale - Self-Hosted API

[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Rescue your bricked Fitbit Aria scale!** A self-hosted replacement for Fitbit's discontinued cloud API. Capture weight, body fat, and impedance data locally in PostgreSQL.

---

## 🎯 The Problem

Fitbit killed their cloud API. Your Aria scale connects via WiFi and POSTs data to `*.fitbit.com` — but with no server responding, it's essentially a brick.

## ✨ The Solution

This project intercepts those HTTP requests, parses the binary protocol, and stores your measurements in a local PostgreSQL database. Your scale thinks everything is fine, and you own your data.

```
┌──────────────┐      ┌─────────────┐      ┌──────────────┐      ┌──────────────┐
│  📟 Aria     │ ───▶ │ 🔀 DNS      │ ───▶ │ 🐳 Docker   │ ───▶ │ 🐘 Postgres  │
│    Scale     │ HTTP │   Redirect  │      │   API       │      │   Database   │
└──────────────┘      └─────────────┘      └──────────────┘      └──────────────┘
                                                                        │
                                                                        ▼
                                                                 ┌──────────────┐
                                                                 │ 📊 Your      │
                                                                 │   Dashboard  │
                                                                 └──────────────┘
```

---

## 🚀 Quick Start

### 1️⃣ Start the Server

```bash
git clone https://github.com/your-repo/Fitbit-Aria-Air-Smart-Scale-Api.git
cd Fitbit-Aria-Air-Smart-Scale-Api

docker-compose up -d
```

### 2️⃣ Configure DNS Redirect

Point `www.fitbit.com` and `api.fitbit.com` to your Docker host:

| Method | How |
|--------|-----|
| **Pi-hole** | Local DNS records → `192.168.1.x` |
| **Router** | Custom DNS override |
| **IoT VLAN** | Dedicated network with custom DNS |

### 3️⃣ Verify & Weigh Yourself

```bash
# Check it's running
curl http://localhost/scale/validate
# Returns: T

# Step on your scale, then check:
curl http://localhost/api/measurements/latest
```

---

## 📊 Data Captured

Every measurement from your scale is stored with full detail:

| Field | Description |
|-------|-------------|
| `weight_kg` / `weight_lbs` | Weight in your preferred unit |
| `body_fat_percent` | Calculated body fat % |
| `impedance` | Bio-electrical impedance (Ω) |
| `timestamp` | When you stepped on the scale |
| `user_id` | Which user profile (0 = guest) |

Plus raw data for nerds: `fat_percent_raw_1`, `fat_percent_raw_2`, `covariance`, `timestamp_raw`

---

## 🔌 Connect Your Dashboard

PostgreSQL is exposed on port `5432`:

```
Host: localhost
Port: 5432
Database: aria
User: aria
Password: aria
```

### Example Queries

```sql
-- 📈 Daily weight trend
SELECT DATE(timestamp) as date, AVG(weight_kg) as weight
FROM measurements
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- 🔋 Check scale battery
SELECT mac_address, battery_percent, last_seen
FROM scales;
```

### Grafana, Metabase, etc.

Just point your favorite dashboard tool at the PostgreSQL instance!

---

## 🛠️ API Reference

### Scale Endpoints (used by Aria)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/scale/validate` | GET | Returns `T` ✓ |
| `/scale/register` | GET | Registers scale |
| `/scale/upload` | POST | Receives weight data |

### Management API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/measurements` | GET | List all measurements |
| `/api/measurements/latest` | GET | Most recent reading |
| `/api/scales` | GET | Registered scales |
| `/api/users` | GET/POST | User profiles |
| `/api/health` | GET | Health check |

```bash
# Filter measurements
curl "http://localhost/api/measurements?user_id=1&limit=10"

# Create a user profile
curl -X POST "http://localhost/api/users?name=Alice&height_cm=165&age=30"
```

---

## 🏠 Home Assistant Integration

```yaml
sensor:
  - platform: rest
    name: "Weight"
    resource: http://192.168.1.100/api/measurements/latest
    value_template: "{{ value_json.weight_kg | round(1) }}"
    unit_of_measurement: "kg"
    scan_interval: 300

  - platform: rest
    name: "Body Fat"
    resource: http://192.168.1.100/api/measurements/latest
    value_template: "{{ value_json.body_fat_percent | round(1) }}"
    unit_of_measurement: "%"
    scan_interval: 300
```

---

## ⚙️ Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WEIGHT_UNIT` | `kg` | `kg`, `lbs`, or `stones` |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose logs |
| `DATABASE_URL` | `postgresql://...` | DB connection string |

---

## 🔒 Security Notes

> ⚠️ The management API is **unauthenticated** by default.

**Recommendations:**
- Deploy on a trusted network only
- Use a reverse proxy with auth for `/api/*`
- Change the default PostgreSQL password

<details>
<summary>Example: Nginx with Basic Auth</summary>

```nginx
location /api/ {
    auth_basic "Aria API";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://aria-api:80;
}

location /scale/ {
    # No auth - scale can't authenticate
    proxy_pass http://aria-api:80;
}
```
</details>

---

## 🐛 Troubleshooting

<details>
<summary><b>Scale not syncing?</b></summary>

1. Verify DNS: `nslookup www.fitbit.com` should return your Docker host IP
2. Check logs: `docker-compose logs -f aria-api`
3. Power cycle scale (remove batteries, wait 10s)
</details>

<details>
<summary><b>CRC warnings in logs?</b></summary>

Some firmware versions have slight protocol variations. Data is still processed — warnings are informational only.
</details>

<details>
<summary><b>Debug failed uploads</b></summary>

```bash
curl "http://localhost/api/raw-uploads?errors_only=true"
```
</details>

---

## 🙏 Credits

Protocol reverse-engineering by the community:
- [micolous/helvetic](https://github.com/micolous/helvetic) — Django implementation with protocol docs
- [ads04r/aria-spoof](https://github.com/ads04r/aria-spoof) — PHP implementation

---

## 📄 License

MIT License — do whatever you want with it!

---

<p align="center">
  <i>Not affiliated with Fitbit or Google. Just keeping hardware out of landfills.</i> ♻️
</p>
