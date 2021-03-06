import atexit
from collections import namedtuple
from functools import wraps
import os
from time import time

from provisioningserver.prometheus import (
    prom_cli,
    PROMETHEUS_SUPPORTED,
)
from twisted.internet.defer import Deferred

# Definition for a Prometheus metric.
MetricDefinition = namedtuple(
    'MetricDefiniition', ['type', 'name', 'description', 'labels'])


class PrometheusMetrics:
    """Wrapper for accessing and interacting with Prometheus metrics."""

    def __init__(self, definitions=None, registry=None):
        if definitions is None:
            self.registry = None
            self._metrics = {}
        else:
            self.registry = registry or prom_cli.REGISTRY
            self._metrics = self._create_metrics(definitions)
        if PROMETHEUS_SUPPORTED and self.registry is prom_cli.REGISTRY:
            atexit.register(self._cleanup_metric_files)

    def _create_metrics(self, definitions):
        metrics = {}
        for definition in definitions:
            labels = definition.labels
            cls = getattr(prom_cli, definition.type)
            metrics[definition.name] = cls(
                definition.name, definition.description, labels,
                registry=self.registry)
        return metrics

    @property
    def available_metrics(self):
        """Return a list of available metric names."""
        return list(self._metrics)

    def update(self, metric_name, action, value=None, labels=None):
        """Update the specified metric."""
        if not self._metrics:
            return

        metric = self._metrics[metric_name]
        if labels:
            metric = metric.labels(**labels)
        func = getattr(metric, action)
        if value is None:
            func()
        else:
            func(value)

    def generate_latest(self):
        """Generate a bytestring with metric values."""
        if self.registry is not None:
            registry = self.registry
            if registry is prom_cli.REGISTRY:
                # when using the global registry, setup up multiprocess
                # support. In this case, a separate registry needs to be used
                # for generating the samples.
                registry = prom_cli.CollectorRegistry()
                from prometheus_client import multiprocess
                multiprocess.MultiProcessCollector(registry)
            return prom_cli.generate_latest(registry)

    def record_call_latency(
            self, metric_name, get_labels=lambda *args, **kwargs: {}):
        """Wrap a function to record its call latency on a metric.

        If the function is asynchronous (it returns a Deferred), the time to
        complete the deferred is tracked.

        The `get_labels` function is called with the same arguments as the call
        and must return a dict with labels for the metric.

        """

        def wrap_func(func):

            @wraps(func)
            def wrapper(*args, **kwargs):
                labels = get_labels(*args, **kwargs)
                before = time()
                result = func(*args, **kwargs)
                after = time()
                if not isinstance(result, Deferred):
                    latency = after - before
                    self.update(
                        metric_name, 'observe', value=latency, labels=labels)
                    return result

                # attach a callback to the deferred to track time after the
                # call has completed
                def record_latency(result):
                    latency = time() - before
                    self.update(
                        metric_name, 'observe', value=latency, labels=labels)
                    return result

                result.addCallback(record_latency)
                return result

            return wrapper

        return wrap_func

    def _cleanup_metric_files(self):
        """Remove prometheus metrics files for the process itself."""
        if 'prometheus_multiproc_dir' not in os.environ:
            return
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(os.getpid())


def create_metrics(metric_definitions, registry=None):
    """Return a PrometheusMetrics from the specified definitions."""
    definitions = metric_definitions if PROMETHEUS_SUPPORTED else None
    return PrometheusMetrics(definitions=definitions, registry=registry)
