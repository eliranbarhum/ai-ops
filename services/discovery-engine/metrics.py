from prometheus_client import Counter, Histogram, Gauge

vuln_scan_duration = Histogram(
    "mco_vuln_scan_duration_seconds",
    "Vulnerability scan duration",
    ["scope"],
    buckets=[5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

vuln_scans_active = Gauge(
    "mco_vuln_scans_active",
    "Number of vulnerability scans currently running",
)

network_scan_duration = Histogram(
    "mco_network_scan_duration_seconds",
    "Network scan duration",
    ["profile"],
    buckets=[5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

network_scans_active = Gauge(
    "mco_network_scans_active",
    "Number of network scans currently running",
)
