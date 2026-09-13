from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "feishu_gateway_http_requests_total",
            "HTTP requests by bounded route and result",
            ("method", "route", "status_class"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "feishu_gateway_http_request_duration_seconds",
            "HTTP request duration",
            ("method", "route"),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.deliveries = Counter(
            "feishu_gateway_deliveries_total",
            "Notification delivery outcomes",
            ("source", "result"),
            registry=self.registry,
        )
        self.delivery_states = Counter(
            "feishu_gateway_delivery_states_total",
            "Notification delivery ledger state transitions",
            ("source", "state"),
            registry=self.registry,
        )
        self.delivery_attempts = Counter(
            "feishu_gateway_delivery_attempts_total",
            "Notification delivery attempts and outcomes",
            ("source", "result"),
            registry=self.registry,
        )
        self.synthetic_prepares = Counter(
            "feishu_gateway_synthetic_prepares_total",
            "Local-only synthetic notification preparations",
            ("result",),
            registry=self.registry,
        )
        self.synthetic_probes = Counter(
            "feishu_gateway_synthetic_probes_total",
            "Local-only synthetic notification probes",
            ("result",),
            registry=self.registry,
        )
        self.external_requests = Counter(
            "feishu_gateway_external_requests_total",
            "External dependency outcomes",
            ("provider", "result"),
            registry=self.registry,
        )
        self.external_duration = Histogram(
            "feishu_gateway_external_request_duration_seconds",
            "External dependency request duration",
            ("provider",),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 90),
            registry=self.registry,
        )
        self.long_connection_up = Gauge(
            "feishu_gateway_long_connection_up",
            "Whether the Feishu long connection worker is running",
            registry=self.registry,
        )
        self.last_success = Gauge(
            "feishu_gateway_last_success_timestamp_seconds",
            "Last successful operation by bounded path",
            ("path",),
            registry=self.registry,
        )
        self.administrative_communications = Counter(
            "feishu_gateway_administrative_communications_total",
            "Governed internal communication outcomes",
            ("result",),
            registry=self.registry,
        )
        self.administrative_communication_states = Counter(
            "feishu_gateway_administrative_communication_states_total",
            "Governed internal communication ledger state transitions",
            ("state",),
            registry=self.registry,
        )
        self.administrative_communication_attempts = Counter(
            "feishu_gateway_administrative_communication_attempts_total",
            "Governed internal communication attempts",
            ("result",),
            registry=self.registry,
        )
