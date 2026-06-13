from prometheus_client import Counter, Histogram, Gauge

pipeline_runs = Counter(
    "mco_pipeline_runs_total",
    "Total pipeline analysis runs",
    ["target", "status"],
)

ad_query_duration = Histogram(
    "mco_ad_query_duration_seconds",
    "AD LDAP query duration",
    ["query_type"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

bulk_ops = Counter(
    "mco_bulk_ops_total",
    "Bulk operation executions",
    ["op_type", "status"],
)
