from prometheus_client import Counter, Histogram

llm_token_count = Counter(
    "mco_llm_token_count_total",
    "Total LLM tokens generated",
    ["model", "endpoint"],
)

llm_request_duration = Histogram(
    "mco_llm_request_duration_seconds",
    "LLM request duration",
    ["model", "endpoint"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)
