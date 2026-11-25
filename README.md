# kantechrh-bridge

Bridge API based on the existing `mundokan-bridge` structure, adapted for KantechRH.

## Runtime

- FastAPI with Uvicorn
- Docker Compose
- Traefik on `api.kantechrh.com.br`
- Hostinger SMTP using `cadastro@kantechrh.com.br`
- Persistent event data in `./data`

## Configuration

Copy `.env.example` to `.env` and fill in the real credentials on the VPS. Never commit `.env` or service-role keys.

## Run

```bash
docker compose up -d --build
```

## Health check

`GET /health` returns `{"status":"healthy"}`.
