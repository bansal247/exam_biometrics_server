# Exam Biometrics – Microservices

```
┌─────────────┐  ┌─────────────────┐  ┌──────────────────┐
│ Admin Panel  │  │ Supervisor Panel│  │  Server (API)    │
│ :3000       ├──┤ :3001           ├──┤ :8000            │
│ HTML/CSS/JS  │  │ HTML/CSS/JS     │  │ FastAPI + Cron   │
└─────────────┘  └─────────────────┘  └───────┬──────────┘
       PUBLIC          PUBLIC                  │ INTERNAL
                                    ┌──────────┴──────────┐
                                    │                     │
                               ┌────┴────┐         ┌─────┴─────┐
                               │ Postgres│         │ SourceAFIS│
                               │ :5432   │         │ :8080     │
                               └─────────┘         └───────────┘
                                 INTERNAL             INTERNAL
```

## Services

| Service          | Port | Exposed | Description                          |
|------------------|------|---------|--------------------------------------|
| server           | 8000 | Yes     | FastAPI API + cron jobs              |
| admin-panel      | 3000 | Yes     | Admin UI (HTML/CSS/JS)               |
| supervisor-panel | 3001 | Yes     | Supervisor UI (HTML/CSS/JS)          |
| db               | 5432 | No      | PostgreSQL 16                        |
| sourceafis       | 8080 | No      | Java fingerprint matching service    |

## Quick Start

```bash
# 1. Generate encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Edit .env

# 3. Start
docker-compose up --build -d

# 4. Access
#    API docs:        http://localhost:8000/docs
#    Admin panel:     http://localhost:3000
#    Supervisor panel: http://localhost:3001
```

## Biometric Storage

All biometric data is stored as encrypted BYTEA in PostgreSQL using Fernet
symmetric encryption. For production scale (100K+ candidates), migrate
images to encrypted S3 and store only keys in the DB.

## SourceAFIS Service

Internal Java microservice wrapping the [SourceAFIS](https://sourceafis.machinezoo.com/) library.

**Endpoints** (internal only):
- `POST /extract` – Extract fingerprint template from BMP image
- `POST /match` – Compare two fingerprint templates
- `GET /health` – Health check

## Cron Jobs (every 30s)

1. **match_pending_photos** – AWS Rekognition face comparison
2. **extract_fingerprint_templates** – SourceAFIS template extraction (batch 10)
3. **extract_iris_templates** – Placeholder (batch 16)
