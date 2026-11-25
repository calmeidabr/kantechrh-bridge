import json
import os
import secrets
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from supabase import Client, create_client

app = FastAPI(title="KantechRH Bridge API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN", "").strip()
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "").strip()
ASAAS_API_URL = os.getenv("ASAAS_API_URL", "https://api.asaas.com/v3").rstrip("/")
APP_PUBLIC_BASE_URL = os.getenv("APP_PUBLIC_BASE_URL", "https://api.kantechrh.com.br").rstrip("/")
APP_LOGIN_URL = os.getenv("APP_LOGIN_URL", "https://app.kantechrh.com.br").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", "")).strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.hostinger.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "cadastro@kantechrh.com.br").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_FILE = DATA_DIR / "processed_events.json"
PLANOS_VALIDOS = {"free", "start", "intermediario", "business", "pro", "alta_performance"}
TABELA_PRECOS = {"start": 99.00, "intermediario": 199.00}

class SignupRequestIn(BaseModel):
    nome_completo: str
    empresa: str
    cnpj: str | None = None
    plano: str
    email_confirmacao: EmailStr
    celular: str
    origem: str | None = None

def load_processed() -> dict:
    if not PROCESSED_FILE.exists():
        return {}
    try:
        return json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_processed(data: dict):
    PROCESSED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def send_email(email_destino: str, assunto: str, mensagem: str):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("SMTP not configured completely")
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = SMTP_FROM
    msg["To"] = email_destino
    msg.set_content(mensagem)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def parse_external_reference(value: str | None) -> dict:
    result = {}
    for part in (value or "").split(";"):
        if "=" in part:
            key, val = part.split("=", 1)
            result[key.strip()] = val.strip()
    return result

@app.get("/")
def root():
    return {"message": "kantechrh bridge online"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/v1/signup-requests")
@app.post("/api/signup-requests")
async def create_signup_request(payload: SignupRequestIn):
    if payload.plano not in PLANOS_VALIDOS:
        raise HTTPException(status_code=400, detail="plano inválido")
    if not supabase:
        raise HTTPException(status_code=500, detail="supabase not configured")
    request_id = str(uuid4())
    token = secrets.token_urlsafe(32)
    row = {"id": request_id, "nome_completo": payload.nome_completo, "empresa": payload.empresa, "cnpj": payload.cnpj, "plano": payload.plano, "email_confirmacao": str(payload.email_confirmacao), "celular": payload.celular, "origem": payload.origem, "status": "pending_validation", "validation_token": token}
    supabase.table("signup_requests").insert(row).execute()
    link = f"{APP_PUBLIC_BASE_URL}/v1/signup-requests/validate?token={token}"
    send_email(str(payload.email_confirmacao), "Confirme sua solicitação no KantechRH", f"Olá, {payload.nome_completo}.\n\nConfirme sua solicitação pelo link:\n{link}\n")
    return {"ok": True, "request_id": request_id, "message": "Enviamos um link de validação para o email informado."}

@app.get("/v1/signup-requests/validate", response_class=HTMLResponse)
@app.get("/api/signup-requests/validate", response_class=HTMLResponse)
async def validate_signup_request(token: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    response = supabase.table("signup_requests").select("*").eq("validation_token", token).execute()
    if not response.data:
        return HTMLResponse("<h2>Token inválido ou expirado.</h2>", status_code=404)
    signup = response.data[0]
    return HTMLResponse(f"<h2>Ativação KantechRH</h2><p>Olá <strong>{signup['nome_completo']}</strong>. Defina sua senha.</p><form action='/v1/signup-requests/set-password' method='POST'><input type='hidden' name='token' value='{token}'><input type='password' name='password' minlength='6' required><input type='password' name='confirm_password' minlength='6' required><button type='submit'>Criar acesso</button></form>")

@app.post("/v1/signup-requests/set-password", response_class=HTMLResponse)
async def set_password_and_provision(token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return HTMLResponse("<h3>As senhas não coincidem.</h3>", status_code=400)
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    res = supabase.table("signup_requests").select("*").eq("validation_token", token).execute()
    if not res.data:
        return HTMLResponse("<h3>Token inválido ou expirado.</h3>", status_code=404)
    signup = res.data[0]
    auth_res = supabase.auth.admin.create_user({"email": signup["email_confirmacao"], "password": password, "email_confirm": True, "user_metadata": {"nome_completo": signup["nome_completo"], "empresa": signup["empresa"], "plano": signup["plano"]}})
    if auth_res.user:
        supabase.table("signup_requests").update({"status": "active", "validated_at": datetime.now(timezone.utc).isoformat()}).eq("id", signup["id"]).execute()
    send_email(signup["email_confirmacao"], "Seu acesso ao KantechRH está ativado", f"Olá {signup['nome_completo']},\n\nSeu acesso está pronto.\n\nLink de login: {APP_LOGIN_URL}\nUsuário: {signup['email_confirmacao']}\n")
    return RedirectResponse(url=APP_LOGIN_URL, status_code=303)

@app.post("/webhooks/asaas")
async def webhook_asaas(request: Request, asaas_access_token: str | None = Header(default=None, alias="asaas-access-token")):
    if not WEBHOOK_TOKEN or asaas_access_token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")
    payload = await request.json()
    event_id = payload.get("id")
    processed = load_processed()
    if event_id in processed:
        return {"received": True, "duplicate": True}
    processed[event_id] = {"event": payload.get("event")}
    save_processed(processed)
    return {"received": True}
