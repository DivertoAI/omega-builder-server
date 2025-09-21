from __future__ import annotations
import threading
import time
from collections import defaultdict
from typing import Dict, Iterable, Tuple, Iterable as IterableT, Optional

# ---------- Primitives ----------

class _Counter:
    def __init__(self, name: str, help_: str = ""):
        self.name = name
        self.help = help_
        self._lock = threading.Lock()
        self._values: Dict[Tuple[Tuple[str, str], ...], int] = defaultdict(int)

    def inc(self, labels: Optional[Dict[str, str]] = None, by: int = 1) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._values[key] += by

    def render(self) -> IterableT[str]:
        if self.help:
            yield f"# HELP {self.name} {self.help}\n# TYPE {self.name} counter\n"
        for labels, v in sorted(self._values.items()):
            if labels:
                label_str = ",".join(f'{k}="{v_}"' for k, v_ in labels)
                yield f"{self.name}{{{label_str}}} {v}\n"
            else:
                yield f"{self.name} {v}\n"


class _Gauge:
    def __init__(self, name: str, help_: str = ""):
        self.name = name
        self.help = help_
        self._lock = threading.Lock()
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = defaultdict(float)

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._values[key] = value

    def inc(self, labels: Optional[Dict[str, str]] = None, by: float = 1.0) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._values[key] += by

    def dec(self, labels: Optional[Dict[str, str]] = None, by: float = 1.0) -> None:
        self.inc(labels=labels, by=-by)

    def render(self) -> IterableT[str]:
        if self.help:
            yield f"# HELP {self.name} {self.help}\n# TYPE {self.name} gauge\n"
        for labels, v in sorted(self._values.items()):
            if labels:
                label_str = ",".join(f'{k}="{v_}"' for k, v_ in labels)
                yield f"{self.name}{{{label_str}}} {v}\n"
            else:
                yield f"{self.name} {v}\n"


class _Histogram:
    DEFAULT_BUCKETS = [0.25, 0.5, 1, 2, 4, 8, 15, 30, 60]  # seconds

    def __init__(self, name: str, help_: str = "", buckets: Optional[IterableT[float]] = None):
        self.name = name
        self.help = help_
        self._lock = threading.Lock()
        self._buckets = list(buckets or self.DEFAULT_BUCKETS)
        self._counts: Dict[Tuple[Tuple[str, str], ...], Dict[float, float]] = defaultdict(lambda: defaultdict(float))
        self._sum: Dict[Tuple[Tuple[str, str], ...], float] = defaultdict(float)
        self._obs: Dict[Tuple[Tuple[str, str], ...], float] = defaultdict(float)

    def observe(self, value_seconds: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._sum[key] += value_seconds
            self._obs[key] += 1
            placed = False
            for b in self._buckets:
                if value_seconds <= b + 1e-12:
                    self._counts[key][b] += 1
                    placed = True
            if not placed:  # +Inf
                self._counts[key][float("inf")] += 1

    def timer(self, labels: Optional[Dict[str, str]] = None):
        start = time.perf_counter()
        def _stop():
            self.observe(time.perf_counter() - start, labels=labels)
        return _stop

    def render(self) -> IterableT[str]:
        if self.help:
            yield f"# HELP {self.name} {self.help}\n# TYPE {self.name} histogram\n"
        all_keys = set(self._counts.keys()) | set(self._sum.keys()) | set(self._obs.keys())
        for key in sorted(all_keys):
            counts = self._counts.get(key, {})
            running = 0.0
            # cumulative buckets
            for b in self._buckets + [float("inf")]:
                running += counts.get(b, 0.0)
                label_str = ",".join(f'{k}="{v_}"' for k, v_ in key)
                le = "+Inf" if b == float("inf") else f"{b:.2f}"
                if label_str:
                    yield f'{self.name}_bucket{{{label_str},le="{le}"}} {running}\n'
                else:
                    yield f'{self.name}_bucket{{le="{le}"}} {running}\n'
            # sum & count
            sum_ = self._sum.get(key, 0.0)
            cnt_ = self._obs.get(key, 0.0)
            label_str = ",".join(f'{k}="{v_}"' for k, v_ in key)
            if label_str:
                yield f"{self.name}_sum{{{label_str}}} {sum_}\n"
                yield f"{self.name}_count{{{label_str}}} {cnt_}\n"
            else:
                yield f"{self.name}_sum {sum_}\n"
                yield f"{self.name}_count {cnt_}\n"


# ---------- Registry ----------

class MetricsRegistry:
    def __init__(self):
        self._items: list[object] = []

    def counter(self, name: str, help_: str = "") -> _Counter:
        c = _Counter(name, help_)
        self._items.append(c)
        return c

    def gauge(self, name: str, help_: str = "") -> _Gauge:
        g = _Gauge(name, help_)
        self._items.append(g)
        return g

    def histogram(self, name: str, help_: str = "", buckets: Optional[IterableT[float]] = None) -> _Histogram:
        h = _Histogram(name, help_, buckets=buckets)
        self._items.append(h)
        return h

    def render_prometheus(self) -> str:
        out: list[str] = []
        for it in self._items:
            out.extend(it.render())
        return "".join(out)

    def reset(self) -> None:
        # Simple re-init; callers will re-get global metric objects on import
        self._items.clear()


REGISTRY = MetricsRegistry()

# ---------- App metrics ----------

build_counter = REGISTRY.counter("omega_build_total", "Count of build attempts by result")
publish_counter = REGISTRY.counter("omega_publish_total", "Count of publish attempts by result")

build_duration = REGISTRY.histogram("omega_build_duration_seconds", "Build duration in seconds")
publish_duration = REGISTRY.histogram("omega_publish_duration_seconds", "Publish duration in seconds")

inflight_builds = REGISTRY.gauge("omega_build_inflight", "Number of builds currently running")

# North Star contract readiness (apps, services, dashboards, design, assets, adapters, infra)
contract_ready = REGISTRY.gauge("omega_contract_ready", "Gauge of contract readiness by dir")