import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
import store
from tester import test_vcenter, test_vrops, test_anthropic, test_openai, test_gemini, test_ollama, test_sddc, test_agent_ollama, test_nsx, test_ad

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("config-store")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    await store._get_pool()  # warm up asyncpg pool at startup
    yield

app = FastAPI(title="MCO Config Store", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

TESTERS = {
    "vcenter": test_vcenter,
    "vrops": test_vrops,
    "anthropic": test_anthropic,
    "openai": test_openai,
    "gemini": test_gemini,
    "ollama": test_ollama,
    "sddc": test_sddc,
    "nsx": test_nsx,
    "ad": test_ad,
    "agent-ollama": test_agent_ollama,
}


class ConfigPayload(BaseModel):
    vcenter_host: str = ""
    vcenter_user: str = "administrator@vsphere.local"
    vcenter_password: str = ""
    vcenter_verify_ssl: bool = False
    vrops_host: str = ""
    vrops_user: str = "admin"
    vrops_password: str = ""
    vrops_verify_ssl: bool = False
    sddc_host: str = ""
    sddc_user: str = "administrator@vsphere.local"
    sddc_password: str = ""
    sddc_verify_ssl: bool = False
    nsx_host: str = ""
    nsx_user: str = "admin"
    nsx_password: str = ""
    nsx_verify_ssl: bool = False
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    vllm_url: str = "http://vllm-server:11434"
    vllm_model: str = "qwen2.5:14b"
    vcf_target_version: str = "9.0"
    ad_host: str = ""
    ad_user: str = ""
    ad_password: str = ""
    ad_domain: str = ""
    agent_llm_provider: str = "anthropic"
    agent_anthropic_api_key: str = ""
    agent_anthropic_model: str = "claude-sonnet-4-6"
    agent_openai_api_key: str = ""
    agent_openai_model: str = "gpt-4o"
    agent_gemini_api_key: str = ""
    agent_gemini_model: str = "gemini-2.0-flash"
    agent_ollama_url: str = "http://vllm-server:11434"
    agent_ollama_model: str = "qwen2.5-coder:7b"


@app.get("/config")
async def get_config():
    """Returns current config with sensitive fields masked."""
    cfg = store.load()
    return store.mask(cfg)


@app.post("/config")
async def save_config(payload: ConfigPayload):
    """
    Save configuration. Sensitive fields with value '••••••••'
    (masked placeholder) are left unchanged from the stored version.
    """
    existing = store.load()
    incoming = payload.model_dump()

    # Never overwrite a saved secret with the masked placeholder
    SENSITIVE = {
        "vcenter_password", "vrops_password", "sddc_password", "nsx_password",
        "anthropic_api_key", "openai_api_key", "gemini_api_key",
        "agent_anthropic_api_key", "agent_openai_api_key", "agent_gemini_api_key",
        "ad_password",
    }
    for field in SENSITIVE:
        if incoming.get(field) == "••••••••":
            incoming[field] = existing.get(field, "")

    store.save(incoming)
    logger.info("Configuration updated")
    return {"ok": True, "message": "Configuration saved"}


@app.post("/config/test/{service}")
async def test_connection(service: str):
    """Test connectivity for a specific integration using stored credentials."""
    if service not in TESTERS:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    cfg = store.load()
    result = await TESTERS[service](cfg)
    return result


@app.get("/config/status")
async def all_status():
    """Test all integrations and return a status map."""
    cfg = store.load()
    results = {}
    for name, tester in TESTERS.items():
        try:
            results[name] = await tester(cfg)
        except Exception as e:
            results[name] = {"ok": False, "message": str(e)}
    return results


@app.get("/config/raw")
async def get_raw():
    """Internal endpoint — returns decrypted config for other services (no masking)."""
    return store.load()


@app.get("/scans")
async def list_scans():
    return {"scans": store.load_scans()}


@app.post("/scans")
async def create_scan(request: Request):
    body = await request.json()
    scan = store.save_scan(
        target=body.get("target", ""),
        query=body.get("query", ""),
        result=body.get("result", {}),
    )
    return scan


@app.delete("/scans/{scan_id}")
async def remove_scan(scan_id: str):
    if not store.delete_scan(scan_id):
        raise HTTPException(status_code=404, detail="Scan not found")
    return {"ok": True}


@app.get("/workspace")
async def list_workspace():
    return {"entries": store.load_workspace()}


@app.post("/workspace")
async def create_workspace_entry(request: Request):
    body = await request.json()
    entry = store.save_workspace_entry(
        description=body.get("description", ""),
        spec=body.get("spec", {}),
        result=body.get("result", {}),
    )
    return entry


@app.delete("/workspace/{entry_id}")
async def remove_workspace_entry(entry_id: str):
    if not store.delete_workspace_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@app.get("/conversations")
async def list_conversations():
    convs = await store.load_conversations()
    return {"conversations": [
        {k: v for k, v in c.items() if k != "messages"}
        for c in convs
    ]}


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    convs = await store.load_conversations()
    conv = next((c for c in convs if c.get("id") == conv_id), None)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.post("/conversations")
async def upsert_conversation(request: Request):
    body = await request.json()
    conv = await store.save_conversation(
        conversation_id=body.get("id", ""),
        title=body.get("title", "New conversation"),
        messages=body.get("messages", []),
        provider=body.get("provider", ""),
    )
    return conv


@app.delete("/conversations/{conv_id}")
async def remove_conversation(conv_id: str):
    if not await store.delete_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.get("/kubectl/pinned")
async def list_pinned_commands(user_id: str = ""):
    return {"commands": store.load_pinned_commands(user_id)}


@app.post("/kubectl/pinned")
async def add_pinned_command(request: Request):
    body = await request.json()
    entry = store.save_pinned_command(
        body.get("user_id", ""), body.get("command", ""), body.get("label", "")
    )
    return entry


@app.delete("/kubectl/pinned/{cmd_id}")
async def remove_pinned_command(cmd_id: str, user_id: str = ""):
    if not store.delete_pinned_command(user_id, cmd_id):
        raise HTTPException(status_code=404, detail="Pinned command not found")
    return {"ok": True}


@app.get("/alert-channels")
async def list_alert_channels():
    return {"channels": store.load_alert_channels()}


@app.post("/alert-channels")
async def create_alert_channel(request: Request):
    body = await request.json()
    return store.save_alert_channel(
        body.get("name", ""), body.get("type", "webhook"), body.get("config", {})
    )


@app.delete("/alert-channels/{channel_id}")
async def remove_alert_channel(channel_id: str):
    if not store.delete_alert_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True}


@app.get("/alert-rules")
async def list_alert_rules():
    return {"rules": store.load_alert_rules()}


@app.post("/alert-rules")
async def create_alert_rule(request: Request):
    body = await request.json()
    return store.save_alert_rule(
        body.get("name", ""), body.get("event_type", ""),
        body.get("condition", {}), body.get("channel_ids", []),
        body.get("enabled", True),
    )


@app.patch("/alert-rules/{rule_id}")
async def patch_alert_rule(rule_id: str, request: Request):
    body = await request.json()
    if not store.update_alert_rule(rule_id, body):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


@app.delete("/alert-rules/{rule_id}")
async def remove_alert_rule(rule_id: str):
    if not store.delete_alert_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


@app.get("/maintenance-windows")
async def list_maintenance_windows():
    return {"windows": store.load_maintenance_windows()}


@app.post("/maintenance-windows")
async def create_maintenance_window(request: Request):
    body = await request.json()
    return store.save_maintenance_window(
        body.get("name", ""),
        int(body.get("day_of_week", 6)),
        int(body.get("start_hour", 2)),
        int(body.get("start_minute", 0)),
        int(body.get("duration_minutes", 120)),
        body.get("enabled", True),
    )


@app.patch("/maintenance-windows/{window_id}")
async def patch_maintenance_window(window_id: str, request: Request):
    body = await request.json()
    if not store.update_maintenance_window(window_id, body):
        raise HTTPException(status_code=404, detail="Window not found")
    return {"ok": True}


@app.delete("/maintenance-windows/{window_id}")
async def remove_maintenance_window(window_id: str):
    if not store.delete_maintenance_window(window_id):
        raise HTTPException(status_code=404, detail="Window not found")
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "config-store"}
