"""Best-effort MLflow spans containing allowlisted metadata, never prompts or keys."""

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from .config import settings

log = logging.getLogger("red_compass.tracing")
current_review = ContextVar("review_trace", default=None)
TOOLS = {
    "get_request",
    "search_policies",
    "get_policy_document",
    "lookup_parcel",
    "lookup_heritage",
    "lookup_environment",
    "submit_decision",
    "fallback",
}


def identifiers(value):
    """Only public corpus identifiers may cross the trace boundary."""
    if isinstance(value, dict):
        return {
            key: item
            for key, item in value.items()
            if key in {"document_id", "parcel_id", "section", "version", "id"}
            and isinstance(item, str)
            and re.fullmatch(
                r"(?:RC-[A-Z]+-\d+|RC-\d{3}|\d+(?:\.\d+)*|(?:parcels|heritage|environment):RC-\d{3})",
                item,
            )
        }
    return {}


def tool_result(name, result, args):
    output = {}
    if name in {"search_policies", "get_policy_document"} and isinstance(result, list):
        output["policies"] = [identifiers(row) for row in result[:12]]
    if name.startswith("lookup_") and isinstance(result, dict):
        output.update(identifiers(result))
        if isinstance(result.get("available"), bool):
            output["available"] = result["available"]
    if name in {"submit_decision", "fallback"}:
        output["decision"] = "deny" if name == "fallback" else args.get("decision")
        output["decision_origin"] = (
            "iteration_limit_fallback" if name == "fallback" else "model"
        )
        output["citations"] = [
            identifiers(citation)
            for reason in args.get("reasons", [])
            if isinstance(reason, dict)
            for citation in reason.get("policy_citations", [])
        ][:30]
    return output


class ReviewTrace:
    def __init__(self, tools):
        self.tools = tools
        self.mlflow = None
        self.root = None
        self.token = None

    def __enter__(self):
        cfg = settings()
        if cfg.mlflow_tracking_uri:
            try:
                import mlflow

                self.mlflow = mlflow
                mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
                mlflow.set_experiment(cfg.mlflow_experiment_name)
                self.root = mlflow.start_span_no_context(
                    name="construction_review",
                    span_type="AGENT",
                    inputs={
                        "ticket_id": self.tools.ticket,
                        "attempt_id": self.tools.attempt,
                    },
                    tags={
                        "ticket_id": self.tools.ticket,
                        "attempt_id": self.tools.attempt,
                        "policy_version": self.tools.corpus,
                        "sandbox": getattr(self.tools, "sandbox", "local"),
                    },
                )
            except Exception:
                log.warning("MLflow trace initialization unavailable; review continues")
        self.token = current_review.set(self)
        return self

    def safe(self, method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            # SDK exception text can contain remote data. Never print it here.
            log.warning("MLflow trace operation unavailable; review continues")

    @contextmanager
    def span(self, name, kind, inputs):
        span = None
        if self.root:
            span = self.safe(
                self.mlflow.start_span_no_context,
                name=name,
                span_type=kind,
                parent_span=self.root,
                inputs=inputs,
            )
        try:
            yield span
        except BaseException as exc:
            if span:
                self.safe(span.set_attribute, "error_type", type(exc).__name__)
                self.safe(span.set_status, "ERROR")
            raise
        finally:
            if span:
                self.safe(span.end)

    def outputs(self, span, output):
        if span:
            self.safe(span.set_outputs, output)

    def model_call(self, create, turn, **kwargs):
        with self.span(
            f"model_turn_{turn}",
            "LLM",
            {
                "turn": turn,
                "model": kwargs["model"],
                "available_tools": [tool["name"] for tool in kwargs["tools"]],
                "decision_only": kwargs["tool_choice"].get("name") == "submit_decision",
            },
        ) as span:
            result = create(**kwargs)
            usage = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            }
            self.outputs(
                span,
                {
                    "requested_tools": [
                        block.name if block.name in TOOLS else "unknown_tool"
                        for block in result.content
                        if block.type == "tool_use"
                    ],
                    **usage,
                },
            )
            if span:
                self.safe(
                    span.set_attribute,
                    "mlflow.chat.tokenUsage",
                    {
                        **usage,
                        "total_tokens": sum(usage.values()),
                    },
                )
            return result

    def __exit__(self, exc_type, exc, tb):
        if self.root:
            if exc_type:
                self.safe(self.root.set_attribute, "error_type", exc_type.__name__)
                self.safe(self.root.set_status, "ERROR")
            self.safe(self.root.end)
            self.safe(self.mlflow.flush_trace_async_logging)
        current_review.reset(self.token)
        return False


def trace_tool(function):
    @wraps(function)
    def wrapped(self, *positional, **kwargs):
        trace = current_review.get()
        if trace is None:
            return function(self, *positional, **kwargs)
        name = (
            "fallback"
            if function.__name__ == "fallback"
            else (positional[0] if positional else kwargs.get("name"))
        )
        args = positional[1] if len(positional) > 1 else kwargs.get("args", {})
        name = name if name in TOOLS else "unknown_tool"
        kind = (
            "RETRIEVER"
            if name in {"search_policies", "get_policy_document"}
            else "TOOL"
        )
        with trace.span(name, kind, identifiers(args)) as span:
            result = function(self, *positional, **kwargs)
            trace.outputs(span, trace.safe(tool_result, name, result, args) or {})
            return result

    return wrapped
