import os
import hashlib
import json
import logging
import time
import asyncio
import httpx
from fastapi import HTTPException
from metrics import llm_token_count, llm_request_duration

logger = logging.getLogger("llm-gateway")

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")

_K8S_HOST = f"https://{os.getenv('KUBERNETES_SERVICE_HOST','kubernetes.default.svc')}:{os.getenv('KUBERNETES_SERVICE_PORT','443')}"
_K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_NS = os.getenv("POD_NAMESPACE", "mco")
_ENV_CONTEXT_CACHE: dict = {}
_ENV_CONTEXT_TS: float = 0.0
_ENV_CONTEXT_TTL: float = 300.0


async def _get_cfg() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {}


async def get_env_context() -> dict:
    global _ENV_CONTEXT_CACHE, _ENV_CONTEXT_TS
    now = time.time()
    if _ENV_CONTEXT_CACHE and (now - _ENV_CONTEXT_TS) < _ENV_CONTEXT_TTL:
        return _ENV_CONTEXT_CACHE
    try:
        token = open(_K8S_TOKEN_PATH).read().strip()
        async with httpx.AsyncClient(verify=_K8S_CA_PATH, timeout=5.0) as c:
            r = await c.get(
                f"{_K8S_HOST}/api/v1/namespaces/{_K8S_NS}/configmaps/mco-env-context",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                ctx = json.loads(data.get("env_context.json", "{}"))
                _ENV_CONTEXT_CACHE = ctx
                _ENV_CONTEXT_TS = now
                return ctx
    except Exception as e:
        logger.debug(f"env_context ConfigMap not available: {e}")
    return _ENV_CONTEXT_CACHE or {}


async def _call_anthropic(cfg: dict, system: str, user: str, max_tokens: int = 1024) -> str:
    import anthropic
    key = cfg.get("anthropic_api_key", "")
    model = cfg.get("anthropic_model", "claude-sonnet-4-6")
    if not key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured — open Settings to add it")
    client = anthropic.Anthropic(api_key=key)
    t0 = time.time()
    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    llm_request_duration.labels(model=model, endpoint="anthropic").observe(time.time() - t0)
    llm_token_count.labels(model=model, endpoint="anthropic").inc(getattr(msg.usage, "output_tokens", 0))
    return msg.content[0].text


async def _call_openai(cfg: dict, system: str, user: str, max_tokens: int = 1024) -> str:
    from openai import OpenAI
    key = cfg.get("openai_api_key", "")
    model = cfg.get("openai_model", "gpt-4o")
    if not key:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured — open Settings to add it")
    client = OpenAI(api_key=key)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    llm_request_duration.labels(model=model, endpoint="openai").observe(time.time() - t0)
    llm_token_count.labels(model=model, endpoint="openai").inc(
        getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
    )
    return resp.choices[0].message.content


async def _call_gemini(cfg: dict, system: str, user: str, max_tokens: int = 1024) -> str:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    key = cfg.get("gemini_api_key", "")
    model = cfg.get("gemini_model", "gemini-2.0-flash")
    if not key:
        raise HTTPException(status_code=503, detail="Gemini API key not configured — open Settings to add it")
    genai.configure(api_key=key)
    client = genai.GenerativeModel(model_name=model, system_instruction=system)
    t0 = time.time()
    resp = client.generate_content(user, generation_config=GenerationConfig(max_output_tokens=max_tokens))
    llm_request_duration.labels(model=model, endpoint="gemini").observe(time.time() - t0)
    try:
        tokens = resp.usage_metadata.candidates_token_count
    except Exception:
        tokens = 0
    llm_token_count.labels(model=model, endpoint="gemini").inc(tokens)
    return resp.text


async def _call_ollama(cfg: dict, system: str, user: str,
                       max_tokens: int = 1024, temperature: float = 0.0) -> str:
    url = cfg.get("vllm_url", "http://vllm-server:11434").rstrip("/")
    model = cfg.get("vllm_model", "qwen2.5:14b")
    t0 = time.time()
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(
            f"{url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "keep_alive": -1,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    llm_request_duration.labels(model=model, endpoint="ollama").observe(time.time() - t0)
    usage = data.get("usage") or {}
    llm_token_count.labels(model=model, endpoint="ollama").inc(usage.get("completion_tokens", 0))
    return data["choices"][0]["message"]["content"]


_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300.0


def _cache_key(scoring_result: dict, raw_data: dict, query: str) -> str:
    versions = raw_data.get("check_broadcom_interop", {}).get("components", {})
    payload = {
        "q": query,
        "score": scoring_result.get("readiness_score"),
        "status": scoring_result.get("status"),
        "risk_count": len(scoring_result.get("risk_factors", [])),
        "versions": versions,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _cache_get(key: str) -> str | None:
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]
    _cache.pop(key, None)
    return None


def _cache_set(key: str, text: str) -> None:
    _cache[key] = (text, time.time())
    if len(_cache) > 100:
        evict = sorted(_cache, key=lambda k: _cache[k][1])[:50]
        for k in evict:
            _cache.pop(k, None)


async def _stream_anthropic(cfg: dict, system: str, user: str):
    import anthropic
    key = cfg.get("anthropic_api_key", "")
    model = cfg.get("anthropic_model", "claude-sonnet-4-6")
    if not key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    client = anthropic.AsyncAnthropic(api_key=key)
    async with client.messages.stream(
        model=model, max_tokens=2048, system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_openai(cfg: dict, system: str, user: str):
    from openai import AsyncOpenAI
    key = cfg.get("openai_api_key", "")
    model = cfg.get("openai_model", "gpt-4o")
    if not key:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")
    client = AsyncOpenAI(api_key=key)
    async with await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=2048, stream=True,
    ) as stream:
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta


async def _stream_gemini(cfg: dict, system: str, user: str):
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    key = cfg.get("gemini_api_key", "")
    model = cfg.get("gemini_model", "gemini-2.0-flash")
    if not key:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")
    genai.configure(api_key=key)
    client = genai.GenerativeModel(model_name=model, system_instruction=system)
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _run():
        try:
            for chunk in client.generate_content(
                user, generation_config=GenerationConfig(max_output_tokens=2048), stream=True
            ):
                text = getattr(chunk, "text", "") or ""
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, _run)
    while True:
        token = await queue.get()
        if token is None:
            break
        yield token


async def _stream_ollama(cfg: dict, system: str, user: str):
    url = cfg.get("vllm_url", "http://vllm-server:11434").rstrip("/")
    model = cfg.get("vllm_model", "qwen2.5:7b")
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream(
            "POST", f"{url}/v1/chat/completions",
            json={"model": model, "stream": True, "max_tokens": 1024,
                  "keep_alive": -1,
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user}]},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        delta = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        pass


def _pick_streamer(provider: str, cfg: dict, system: str, user: str):
    if provider == "anthropic":
        return _stream_anthropic(cfg, system, user)
    if provider == "openai":
        return _stream_openai(cfg, system, user)
    if provider == "gemini":
        return _stream_gemini(cfg, system, user)
    if provider == "ollama":
        return _stream_ollama(cfg, system, user)
    raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
