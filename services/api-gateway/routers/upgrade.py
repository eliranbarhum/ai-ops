"""
VCF Upgrade Sequencing Assistant.

Aggregates SDDC upgrade ordering data, enriches blockers with LLM explanations,
computes a rollback risk score, and returns a ready-to-export runbook.
"""
from __future__ import annotations
import os
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from shared import LLM_GATEWAY_URL, _proxy

logger = logging.getLogger("api-gateway")
router = APIRouter()

_SDDC_COLLECTOR = os.getenv("SDDC_COLLECTOR_URL", "http://collector-sddc:8011")
_VCENTER_COLLECTOR = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")
_VROPS_COLLECTOR = os.getenv("VROPS_COLLECTOR_URL", "http://collector-vrops:8004")

# In-process explanation cache (blockers don't change rapidly)
_explain_cache: dict[str, str] = {}


async def _llm_explain_blockers(blockers: list[dict]) -> list[dict]:
    """Pass raw SDDC blockingReasons through LLM for plain-English explanation + remediation."""
    if not blockers:
        return blockers

    cache_key = hashlib.md5(json.dumps(blockers, sort_keys=True).encode()).hexdigest()
    if cache_key in _explain_cache:
        cached = json.loads(_explain_cache[cache_key])
        for blocker, explained in zip(blockers, cached):
            blocker["explanation"] = explained.get("explanation", "")
            blocker["remediation"] = explained.get("remediation", "")
        return blockers

    reasons_text = "\n".join(
        f"- {b['component_type']} {b.get('current_version', '')}: {', '.join(b.get('blocking_reasons', [])) or 'No reasons provided'}"
        for b in blockers
    )

    system = (
        "You are a VMware VCF expert. Given raw SDDC Manager upgrade blockers, "
        "return a JSON array with one object per blocker. Each object must have:\n"
        "  explanation: 1-2 sentence plain-English explanation of what is blocking and why\n"
        "  remediation: concise action the IT admin must take to resolve it\n"
        "Return only valid JSON array, no markdown."
    )
    user = f"Blockers to explain:\n{reasons_text}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            cfg_resp = await client.get(f"{os.getenv('CONFIG_STORE_URL', 'http://config-store:8009')}/config/raw")
            cfg = cfg_resp.json() if cfg_resp.status_code == 200 else {}
            provider = cfg.get("llm_provider", "anthropic")

            if provider == "anthropic":
                api_key = cfg.get("anthropic_api_key", "")
                if not api_key:
                    raise ValueError("No Anthropic API key configured")
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1024,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                )
                resp.raise_for_status()
                text = resp.json()["content"][0]["text"]
            elif provider == "ollama":
                ollama_url = cfg.get("ollama_url", "http://vllm-server:11434")
                model = cfg.get("ollama_model", "llama3")
                resp = await client.post(
                    f"{ollama_url}/api/generate",
                    json={"model": model, "prompt": f"{system}\n\n{user}", "stream": False},
                )
                resp.raise_for_status()
                text = resp.json().get("response", "[]")
            else:
                raise ValueError(f"Provider {provider} not supported for blocker explanation")

            # Extract JSON from response
            start = text.find("[")
            end = text.rfind("]") + 1
            explanations = json.loads(text[start:end]) if start >= 0 else []
            _explain_cache[cache_key] = json.dumps(explanations)

            for blocker, explained in zip(blockers, explanations):
                blocker["explanation"] = explained.get("explanation", "")
                blocker["remediation"] = explained.get("remediation", "")

    except Exception as e:
        logger.warning(f"LLM blocker explanation failed: {e}")
        for blocker in blockers:
            blocker.setdefault("explanation", "")
            blocker.setdefault("remediation", "")

    return blockers


def _compute_rollback_risk(sequence: dict, hosts: list, clusters: list, powered_on_vms: int) -> dict:
    """
    Enhanced rollback risk: 0–100 composite score.
    Factors: blocker presence, host count, degraded clusters, active VMs needing vMotion.
    """
    score = 0
    reasons = []

    total_blockers = sequence.get("total_blockers", 0)
    if total_blockers:
        score += min(total_blockers * 10, 35)
        reasons.append(f"{total_blockers} upgrade blocker(s) must be resolved first")

    host_count = len(hosts)
    if host_count > 10:
        score += 20
        reasons.append(f"{host_count} ESXi hosts require reboots during host upgrades")
    elif host_count > 4:
        score += 10
        reasons.append(f"{host_count} ESXi hosts require reboots")

    degraded = [c for c in clusters if c.get("status", "").upper() not in ("ACTIVE", "NORMAL", "")]
    if degraded:
        score += 25
        reasons.append(f"{len(degraded)} cluster(s) already in degraded state — reduced redundancy during upgrade")

    if powered_on_vms > 200:
        score += 15
        reasons.append(f"{powered_on_vms} powered-on VMs require vMotion during host upgrades")
    elif powered_on_vms > 50:
        score += 8
        reasons.append(f"{powered_on_vms} powered-on VMs require vMotion during host upgrades")

    domain_count = sequence.get("total_domains", 1)
    if domain_count > 3:
        score += 5
        reasons.append(f"{domain_count} domains to sequence — complex multi-domain upgrade")

    score = min(score, 100)
    level = "high" if score >= 65 else "medium" if score >= 35 else "low"

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
        "host_count": host_count,
        "degraded_cluster_count": len(degraded),
        "powered_on_vm_count": powered_on_vms,
    }


def _estimate_window(steps: list, host_count: int, powered_on_vms: int) -> str:
    """Rough upgrade window estimate based on component count and environment size."""
    base_hours = 0
    for step in steps:
        for comp in step.get("components_to_upgrade", []):
            ctype = comp["component_type"].upper()
            if "ESXI" in ctype or "HOST" in ctype:
                base_hours += max(1, host_count * 0.5)
            elif "VCENTER" in ctype:
                base_hours += 2
            elif "NSX" in ctype:
                base_hours += 3
            elif "SDDC" in ctype:
                base_hours += 1.5
            else:
                base_hours += 1
    if powered_on_vms > 100:
        base_hours += 1  # vMotion overhead
    total = max(2, round(base_hours))
    if total <= 4:
        return f"~{total} hours"
    elif total <= 8:
        return f"~{total} hours (plan a maintenance window)"
    else:
        return f"~{total} hours — consider splitting across multiple maintenance windows"


def _build_runbook_md(plan: dict) -> str:
    lines = [
        f"# VCF Upgrade Runbook",
        f"",
        f"**Generated:** {plan['generated_at']}",
        f"**SDDC Version:** {plan['sddc_version']}",
        f"**Estimated Window:** {plan['estimated_window']}",
        f"**Rollback Risk:** {plan['rollback_risk']['level'].upper()} ({plan['rollback_risk']['score']}/100)",
        f"",
        f"## Pre-flight Checklist",
        f"",
        f"- [ ] All clusters report ACTIVE / NORMAL status",
        f"- [ ] vSAN resync complete (0% outstanding)",
        f"- [ ] Maintenance window scheduled and communicated",
        f"- [ ] Backup completed and verified",
        f"- [ ] SDDC Manager reachable and healthy",
        f"- [ ] NSX Manager reachable and cluster status green",
        f"",
    ]

    if plan.get("blockers_present"):
        lines += [
            f"## ⚠️ Active Blockers — Resolve Before Proceeding",
            f"",
        ]
        for step in plan["steps"]:
            for b in step.get("blockers", []):
                lines.append(f"### {b['component_type']} {b.get('current_version', '')}")
                if b.get("explanation"):
                    lines.append(f"**Issue:** {b['explanation']}")
                if b.get("remediation"):
                    lines.append(f"**Action:** {b['remediation']}")
                if b.get("blocking_reasons"):
                    lines.append(f"**Raw reasons:** {', '.join(b['blocking_reasons'])}")
                lines.append("")

    lines.append("## Upgrade Steps")
    lines.append("")

    for step in plan["steps"]:
        lines.append(f"### Step {step['step']}: {step['domain_name']} ({step['domain_type']})")
        lines.append("")
        if not step["components_to_upgrade"]:
            lines.append("_No components to upgrade in this domain._")
        else:
            for comp in step["components_to_upgrade"]:
                lines.append(
                    f"{comp['order']}. **{comp['component_type']}** "
                    f"{comp['current_version']} → {comp['target_version']}"
                )
        lines.append("")
        lines.append("**Rollback checkpoint:** Verify cluster health and vSAN before proceeding to next step.")
        lines.append("")

    lines += [
        "## Post-upgrade Verification",
        "",
        "- [ ] All services report healthy in SDDC Manager",
        "- [ ] vCenter accessible and all hosts connected",
        "- [ ] NSX Manager cluster status: STABLE",
        "- [ ] VMs migrated back to preferred hosts (if DRS affinity rules used)",
        "- [ ] Run MCO fleet analysis and verify score ≥ 80",
        "",
    ]
    return "\n".join(lines)


@router.get("/api/v1/upgrade/plan")
async def upgrade_plan(explain: bool = True):
    """
    Full VCF upgrade sequencing plan with rollback risk, LLM-explained blockers,
    and estimated maintenance window.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        async def _get(url: str) -> dict:
            try:
                r = await client.get(url)
                return r.json() if r.status_code == 200 else {}
            except Exception:
                return {}

        sequence, hosts_raw, clusters_raw, system = await asyncio.gather(
            _get(f"{_SDDC_COLLECTOR}/collect/upgrade-sequence"),
            _get(f"{_SDDC_COLLECTOR}/collect/hosts"),
            _get(f"{_SDDC_COLLECTOR}/collect/clusters"),
            _get(f"{_SDDC_COLLECTOR}/collect/system"),
        )

    hosts = hosts_raw.get("hosts", [])
    clusters = clusters_raw.get("clusters", [])
    sddc_version = system.get("version", "unknown")

    if not sequence or "steps" not in sequence:
        return {
            "error": "SDDC Manager not configured or unreachable",
            "steps": [],
            "safe_to_proceed": False,
        }

    # Gather powered-on VM count from hosts data
    powered_on_vms = sum(h.get("vm_count", 0) for h in hosts)

    # Enrich blockers with LLM explanations
    steps = sequence.get("steps", [])
    if explain:
        all_blockers = [b for step in steps for b in step.get("blockers", [])]
        if all_blockers:
            try:
                await asyncio.wait_for(_llm_explain_blockers(all_blockers), timeout=25.0)
            except asyncio.TimeoutError:
                logger.warning("LLM blocker explanation timed out — returning raw reasons")
            # Rebuild per-step blockers from enriched all_blockers list
            idx = 0
            for step in steps:
                n = len(step.get("blockers", []))
                step["blockers"] = all_blockers[idx:idx + n]
                idx += n

    rollback_risk = _compute_rollback_risk(sequence, hosts, clusters, powered_on_vms)
    estimated_window = _estimate_window(steps, len(hosts), powered_on_vms)

    blockers_present = sequence.get("total_blockers", 0) > 0

    verdict = {
        "safe": not blockers_present and rollback_risk["level"] != "high",
        "confidence": (
            "high" if not blockers_present and rollback_risk["level"] == "low" else
            "medium" if not blockers_present else
            "low"
        ),
        "summary": (
            "Ready to upgrade. No blockers detected and risk is low." if not blockers_present and rollback_risk["level"] == "low" else
            f"Proceed with caution. Risk is {rollback_risk['level']}." if not blockers_present else
            f"{sequence['total_blockers']} blocker(s) must be resolved before upgrading."
        ),
    }

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sddc_version": sddc_version,
        "steps": steps,
        "total_domains": sequence.get("total_domains", 0),
        "total_upgradable": sequence.get("total_upgradable", 0),
        "total_blockers": sequence.get("total_blockers", 0),
        "blockers_present": blockers_present,
        "safe_to_proceed": sequence.get("safe_to_proceed", False),
        "rollback_risk": rollback_risk,
        "estimated_window": estimated_window,
        "verdict": verdict,
    }

    plan["runbook_md"] = _build_runbook_md(plan)
    return plan
