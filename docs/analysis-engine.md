# Analysis Engine — Signals, Sources, and Trust Model

Last updated: 2026-06-11, after the ground-truth audit of all 4 scan types.

## Why this document exists

On 2026-06-10 we ran all four scans and compared every scored signal against raw
collector data. The score was right by accident: 65 of 100 points were computed
from wrong or non-existent metrics. This document records what each signal
*actually* measures now, so future changes keep the engine honest.

## The audit — what was broken

| Signal | What it claimed | What it actually was |
|---|---|---|
| CPU 25pts | cluster utilization | vROps `cpu\|workload` demand badge (not utilization) + fake 0% from vCenter |
| RAM 25pts | memory pressure | `mem\|active` touch-rate (3.1%) — real consumed was ~24% |
| Storage 15pts | I/O latency | `diskspace\|latency_average` — **metric does not exist**, always 0 → always perfect |
| Hosts 10pts | host health | tool crashed on `None > 0` for months → always "data unavailable" |
| Platform 15pts | log anomalies | 25 failed BUNDLE_DOWNLOAD tasks classified `info` → score 15/15 "healthy" |
| Network 10pts | port exposure | port 514 (syslog on vCenter/Avi/log servers) flagged as critical rsh — 9 false criticals drove score to 0 |
| HA/DRS | (unused) | collector read nested keys that don't exist → always `false`; real values were `true` |
| vm_count in rollback risk | powered-on VMs | counted per-VM entities that never exist → always 0 (real: 222) |

## Current signal map (vcf_readiness, 100 pts)

| Sub-score | Pts | Source of truth | Notes |
|---|---|---|---|
| CPU Headroom | 20 | vROps `cpu\|usage_average` **per host** (merge in `get_esxi_metrics`) | worst host governs; ≥40-point spread across ≥3 hosts → imbalance warning (DRS-aware) |
| Memory Headroom | 20 | vROps `mem\|usage_average` per host (consumed, not active) | same imbalance logic |
| Storage | 15 | vCenter datastores (capacity) + vROps latency *when present* | vSAN warn 65%/crit 80% (rebuild slack), VMFS 75/90; latency missing → scored on capacity only, never fake 0 |
| Platform Health | 15 | SDDC failed tasks, deduped by pattern | ≥3 repeats of same failure escalates info→warning; BUNDLE_DOWNLOAD/DEPOT failures called out as "upgrade bundles cannot be staged" |
| Host Health | 10 | vCenter connection state + version comparison | mixed ESXi builds → warning; inventory hosts are the fallback when the metrics tool fails |
| HCL & Compatibility | 10 | Broadcom interop matrix + SDDC blockers | unchanged |
| Resilience | 5 | vCenter cluster config | HA off → critical (-3), DRS off → warning (-1), <3 hosts → warning |
| Network Security | 5 | discovery engine port findings | findings grouped by port; 23/21/512/513 critical; **514 = "verify syslog vs rsh" warning**, never critical; unscanned → full score but flagged `data_missing` |

Per-target sub-scorer sets are in `SUBSCORERS_BY_TARGET` (scorer.py). All scores
normalize to 0–100; READY ≥80, WARNING ≥50.

## Trust rules (do not regress these)

1. **A missing metric is `data_missing`, never a healthy zero.** The UI shows
   "scored on N/M signals"; `_no_data()` awards half points.
2. **Per-host beats per-cluster.** The cluster average (36%) hid one idle host
   and three at ~50%.
3. **One problem = one risk factor.** Findings aggregate by pattern/port with
   affected-host lists; 25 identical task failures are *one* systemic problem.
4. **Repeated failure is worse than one failure**, not background noise.
5. **The LLM explains, it never measures.** Prompts forbid inventing anomalies;
   values without an `← ANOMALY` marker are stated as normal.
6. **Say which metric was used.** vROps collector returns `_metrics_used`
   so evidence can name the statKey behind every number.

## Data flow

```
collectors (vcenter / vcf-operations / sddc / logs / discovery)
    → tools service (normalizers → canonical entities + evidence)
        → orchestrator (INTENT_TOOL_MAP per target, parallel fan-out)
            → scoring-engine (deterministic, scorer.py)
            → llm-gateway (/explain — interprets the scored result, RAG-augmented)
```

Tool result caching: `tool_cache.py` (30s–600s per tool).
Scoring thresholds: env-tunable (`SCORE_CPU_WARN` etc., see `_thresholds()`).
