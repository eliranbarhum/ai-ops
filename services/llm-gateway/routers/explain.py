import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from providers import _get_cfg, _call_anthropic, _call_openai, _call_gemini, _call_ollama, _pick_streamer
from providers import _cache_key, _cache_get, _cache_set
from prompts import build_prompt, build_system_prompt
from rag import vcf_docs

logger = logging.getLogger("llm-gateway")
router = APIRouter()


class ChatRequest(BaseModel):
    prompt: str
    system: str = "You are a helpful Kubernetes and cloud infrastructure assistant."
    max_tokens: int = 1024


@router.post("/chat")
async def chat(request: ChatRequest):
    """General-purpose single-turn LLM call. Returns {"text": "..."}."""
    from fastapi import HTTPException
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")
    try:
        if provider == "anthropic":
            text = await _call_anthropic(cfg, request.system, request.prompt, request.max_tokens)
        elif provider == "openai":
            text = await _call_openai(cfg, request.system, request.prompt, request.max_tokens)
        elif provider == "gemini":
            text = await _call_gemini(cfg, request.system, request.prompt, request.max_tokens)
        elif provider == "ollama":
            text = await _call_ollama(cfg, request.system, request.prompt)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
        return {"text": text}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat error ({provider}): {e}")
        raise HTTPException(status_code=502, detail=str(e))


class ExplainRequest(BaseModel):
    scoring_result: dict
    raw_data: dict
    query: str
    target: str = "vcf_readiness"


def _build_explain_prompts(request: ExplainRequest, doc_context: str) -> tuple[str, str]:
    system = build_system_prompt(target=request.target, vcf_doc_context=doc_context)
    user = build_prompt(
        target=request.target,
        score=request.scoring_result.get("readiness_score", 0),
        status=request.scoring_result.get("status", "UNKNOWN"),
        risk_factors=request.scoring_result.get("risk_factors", []),
        recommendations=request.scoring_result.get("recommendations", []),
        query=request.query,
        raw_data=request.raw_data,
    )
    return system, user


def _rag_context(request: ExplainRequest) -> str:
    # RAG only helps vcf_readiness — capacity/anomaly/network don't need upgrade docs
    if request.target not in ("vcf_readiness",):
        return ""
    doc_chunks = vcf_docs.search(request.query, top_k=4)
    if any(kw in request.query.lower() for kw in ("upgrade", "readiness", "vcf 9", "path", "blocker")):
        seen = {c["id"] for c in doc_chunks}
        doc_chunks += [c for c in vcf_docs.search(request.query, top_k=3, source_filter="upgrade")
                       if c["id"] not in seen]
    return vcf_docs.format_for_prompt(doc_chunks[:5])


@router.post("/explain")
async def explain(request: ExplainRequest):
    from fastapi import HTTPException
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")

    key = _cache_key(request.scoring_result, request.raw_data, request.query)
    cached = _cache_get(key)
    if cached:
        return {"explanation": cached}

    doc_context = _rag_context(request)
    system, user = _build_explain_prompts(request, doc_context)

    try:
        logger.info(f"explain: provider={provider}")
        if provider == "anthropic":
            text = await _call_anthropic(cfg, system, user)
        elif provider == "openai":
            text = await _call_openai(cfg, system, user)
        elif provider == "gemini":
            text = await _call_gemini(cfg, system, user)
        elif provider == "ollama":
            text = await _call_ollama(cfg, system, user, temperature=0.4)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown LLM provider: {provider}")
        _cache_set(key, text)
        return {"explanation": text}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM error ({provider}): {e}")
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")


@router.post("/explain/stream")
async def explain_stream(request: ExplainRequest):
    from fastapi import HTTPException
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")

    key = _cache_key(request.scoring_result, request.raw_data, request.query)
    cached = _cache_get(key)
    if cached:
        async def _cached():
            yield f"data: {json.dumps({'type': 'token', 'text': cached})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(_cached(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    doc_context = _rag_context(request)
    system, user = _build_explain_prompts(request, doc_context)

    async def _generate():
        full_text = ""
        try:
            streamer = _pick_streamer(provider, cfg, system, user)
            async for token in streamer:
                full_text += token
                yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
            _cache_set(key, full_text)
        except HTTPException as e:
            yield f"data: {json.dumps({'type': 'error', 'message': e.detail})}\n\n"
        except Exception as e:
            logger.error(f"stream error ({provider}): {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
