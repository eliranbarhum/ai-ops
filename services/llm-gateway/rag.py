"""
Multi-source BM25 RAG for the MCO LLM Gateway.

Sources:
  1. VCenterAPIRetriever   — curated vCenter REST endpoints (vcenter_api_spec.json)
  2. VCFDocsRetriever      — VCF 9.1 documentation chunks (vcf_91_chunks.json)
                             covers: release notes, new APIs/PowerCLI, BOM, upgrade procedures
  3. SDDCAPIRetriever      — SDDC Manager OpenAPI endpoints extracted from vcf_91_chunks.json

All indexes are built once at module load time.
"""

import json
import logging
from pathlib import Path

from rank_bm25 import BM25Okapi

logger = logging.getLogger("llm-gateway.rag")

_SPEC_PATH   = Path(__file__).parent / "vcenter_api_spec.json"
_CHUNKS_PATH = Path(__file__).parent / "vcf_91_chunks.json"


def _tokenize(text: str) -> list[str]:
    return (
        text.lower()
        .replace("/", " ").replace("_", " ").replace(".", " ")
        .replace("{", "").replace("}", "").replace("-", " ")
        .split()
    )


# ─────────────────────────────────────────────────────────────────────────────
# vCenter API Retriever (original, unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class VCenterAPIRetriever:
    def __init__(self):
        spec = json.loads(_SPEC_PATH.read_text())
        self.endpoints: list[dict] = spec["endpoints"]

        corpus = []
        for ep in self.endpoints:
            parts = [
                ep.get("method", ""),
                ep.get("path", ""),
                ep.get("description", ""),
                ep.get("returns", ""),
                " ".join(ep.get("use_when", [])),
            ]
            corpus.append(_tokenize(" ".join(parts)))

        self.bm25 = BM25Okapi(corpus)
        logger.info(f"vCenter API RAG: {len(self.endpoints)} endpoints")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        tokens = _tokenize(query)
        scores = self.bm25.get_scores(tokens)
        indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [self.endpoints[i] for i in indices if scores[i] > 0]

    def format_for_prompt(self, endpoints: list[dict]) -> str:
        if not endpoints:
            return ""
        lines = ["RETRIEVED ENDPOINTS (most relevant to this request — use these as ground truth):"]
        for ep in endpoints:
            lines.append(f"\n  {ep['method']} {ep['path']}")
            lines.append(f"  Description: {ep['description']}")
            if ep.get("query_params"):
                params = ", ".join(ep["query_params"].keys())
                lines.append(f"  Valid query_params: {params}")
            else:
                lines.append(f"  Valid query_params: none — always use {{}}")
            lines.append(f"  Returns: {ep.get('returns', 'see description')}")
            if ep.get("example_output"):
                lines.append(f"  Example output: {json.dumps(ep['example_output'])}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# VCF 9.1 Documentation Retriever
# ─────────────────────────────────────────────────────────────────────────────

class VCFDocsRetriever:
    """
    BM25 search over VCF 9.1 documentation chunks.
    Covers release notes, new APIs/PowerCLI, Bill of Materials, and upgrade procedures.
    Used to inject authoritative context into readiness analysis and workspace prompts.
    """

    def __init__(self):
        if not _CHUNKS_PATH.exists():
            logger.warning(f"VCF docs chunks not found at {_CHUNKS_PATH} — doc RAG disabled")
            self.chunks = []
            self.bm25 = None
            return

        all_chunks: list[dict] = json.loads(_CHUNKS_PATH.read_text())
        # Separate doc chunks from SDDC API chunks (SDDCAPIRetriever handles those)
        self.chunks = [c for c in all_chunks if c.get("source") != "sddc_api"]

        corpus = [_tokenize(c.get("text", "") + " " + c.get("title", ""))
                  for c in self.chunks]
        self.bm25 = BM25Okapi(corpus)
        logger.info(f"VCF docs RAG: {len(self.chunks)} chunks "
                    f"({sum(1 for c in self.chunks if c['source']=='release_notes')} release_notes, "
                    f"{sum(1 for c in self.chunks if c['source']=='bom')} bom, "
                    f"{sum(1 for c in self.chunks if c['source']=='upgrade')} upgrade)")

    def search(self, query: str, top_k: int = 4, source_filter: str | None = None) -> list[dict]:
        if not self.bm25 or not self.chunks:
            return []
        pool = self.chunks
        if source_filter:
            pool = [c for c in self.chunks if c.get("source") == source_filter]
            if not pool:
                pool = self.chunks

        tokens = _tokenize(query)
        if pool is self.chunks:
            scores = self.bm25.get_scores(tokens)
            indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            return [self.chunks[i] for i in indices if scores[i] > 0]
        else:
            # Re-score against filtered subset
            sub_corpus = [_tokenize(c.get("text", "") + " " + c.get("title", "")) for c in pool]
            sub_bm25 = BM25Okapi(sub_corpus)
            scores = sub_bm25.get_scores(tokens)
            indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            return [pool[i] for i in indices if scores[i] > 0]

    def format_for_prompt(self, chunks: list[dict], label: str = "VCF 9.1 DOCUMENTATION CONTEXT") -> str:
        if not chunks:
            return ""
        lines = [f"═══ {label} (authoritative — use this over general knowledge) ═══"]
        for c in chunks:
            source_tag = c.get("source", "").replace("_", " ").title()
            page = c.get("page")
            title = c.get("title", "")
            header = f"[{source_tag}" + (f" p.{page}" if page else "") + (f" — {title}" if title else "") + "]"
            lines.append(f"\n{header}")
            lines.append(c.get("text", "")[:800])
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SDDC Manager API Retriever
# ─────────────────────────────────────────────────────────────────────────────

class SDDCAPIRetriever:
    """
    BM25 search over SDDC Manager REST API endpoints.
    Used when the user asks for SDDC Manager / VCF lifecycle API calls in Workspace.
    """

    def __init__(self):
        if not _CHUNKS_PATH.exists():
            logger.warning("VCF chunks not found — SDDC API RAG disabled")
            self.chunks = []
            self.bm25 = None
            return

        all_chunks: list[dict] = json.loads(_CHUNKS_PATH.read_text())
        self.chunks = [c for c in all_chunks if c.get("source") == "sddc_api"]

        corpus = [_tokenize(c.get("text", "")) for c in self.chunks]
        self.bm25 = BM25Okapi(corpus)
        logger.info(f"SDDC API RAG: {len(self.chunks)} endpoint chunks")

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.bm25 or not self.chunks:
            return []
        tokens = _tokenize(query)
        scores = self.bm25.get_scores(tokens)
        indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [self.chunks[i] for i in indices if scores[i] > 0]

    def format_for_prompt(self, chunks: list[dict]) -> str:
        if not chunks:
            return ""
        lines = ["SDDC MANAGER API ENDPOINTS (from VCF 9.1 OpenAPI spec — use these for SDDC Manager calls):"]
        seen_tags: set[str] = set()
        for c in chunks:
            tag = c.get("tag", "")
            method = c.get("method", "")
            path = c.get("path", "")
            text = c.get("text", "")
            if method and path:
                lines.append(f"\n  {method} /sddc-manager{path}")
                # Extract summary from text
                for line in text.split("\n"):
                    if line.startswith("Summary:"):
                        lines.append(f"  {line}")
                        break
            elif tag not in seen_tags:
                seen_tags.add(tag)
                # Tag-level summary chunk
                ep_lines = [l for l in text.split("\n") if l.startswith("  ")][:5]
                lines.append(f"\n[SDDC Manager — {tag}]")
                lines.extend(ep_lines)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Singletons — built once at import time
# ─────────────────────────────────────────────────────────────────────────────

retriever     = VCenterAPIRetriever()
vcf_docs      = VCFDocsRetriever()
sddc_api      = SDDCAPIRetriever()
