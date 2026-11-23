"""
Deterministic guardrails for the currency agent.

Small local models occasionally skip a tool, use the budget as the trip cost,
or return numbers that contradict their own tool results. This module parses
the user's query, recomputes the answer with the same deterministic tools, and
replaces the model output only when the model's structured result is missing,
inconsistent, or disagrees with the deterministic calculation.

The fallback never blocks a request: when the query does not contain enough
information to recompute, the model's answer is returned unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from . import rates
from .models import TraceEvent
from .rag import DestinationCostGuide
from .tools import (
    _normalize_currency,
    compute_trip_estimate,
    format_estimate,
)

logger = logging.getLogger(__name__)

_CURRENCY = (
    r"(?:₹|rs\.?|inr|rupees?|€|eur|euros?|\$|usd|us\$|dollars?|£|gbp|pounds?)"
)
_AMOUNT = r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<k>k)?"

_BUDGET_PATTERNS = (
    rf"\bbudget\b(?:\s*(?:is|of|at|around|about|up\s+to|:))?\s*"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
    rf"\b(?:i|we)\s+have\s+(?:a\s+budget\s+of\s+)?(?:only\s+)?"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
    rf"\b(?:can\s+afford|afford|spend)\s+(?:up\s+to\s+|only\s+)?"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
)

_COST_PATTERNS = (
    rf"\b(?:trip|travel|total|estimated|overall)?\s*costs?\b"
    rf"(?:\s*(?:is|are|of|at|around|about|approximately|:))?\s*"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
    rf"\bprice(?:d)?\b(?:\s*(?:is|of|at|around|about|:))?\s*"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
    rf"\b(?:booked|quoted|priced|available)\s+(?:at|for)\s+"
    rf"(?P<curr1>{_CURRENCY})?\s*{_AMOUNT}\s*(?P<curr2>{_CURRENCY})?",
)

@lru_cache(maxsize=1)
def _known_destinations() -> tuple[str, ...]:
    return tuple(sorted(DestinationCostGuide().destinations(), key=len, reverse=True))


_DESTINATION_PATTERNS = (
    re.compile(
        r"\b(?:visit|visiting|explore|exploring|see|seeing)\s+"
        r"(?P<name>[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:trip|travel|holiday|vacation)\s+to\s+"
        r"(?P<name>[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bto\s+(?!(?:visit|see|explore|travel|go|stay|spend)\b)"
        r"(?P<name>[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,2})",
        re.IGNORECASE,
    ),
)

_DESTINATION_STOPWORDS = {
    "a", "an", "and", "about", "around", "budget", "cost", "costs", "day",
    "days", "dollars", "during", "eur", "euros", "feasible", "for", "from",
    "gbp", "in", "inr", "is", "it", "me", "month", "my", "next", "night",
    "nights", "on", "our", "people", "person", "persons", "pounds", "rupees",
    "that", "the", "this", "travel", "traveler", "travelers", "traveller",
    "travellers", "trip", "under", "us", "usd", "week", "with", "within",
    "year",
}

_DAYS_RE = re.compile(r"\b(\d{1,2})\s*(?:-|\s)?\s*days?\b", re.IGNORECASE)
_NIGHTS_RE = re.compile(r"\b(\d{1,2})\s*(?:-|\s)?\s*nights?\b", re.IGNORECASE)
_TRAVELERS_RE = re.compile(
    r"\b(\d{1,2})\s*(?:travelers?|travellers?|people|persons?|adults?|guests?)\b"
    r"|\bfor\s+(\d{1,2})\b(?!\s*(?:days?|nights?))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedQuery:
    budget: float | None = None
    budget_currency: str | None = None
    cost: float | None = None
    cost_currency: str | None = None
    destination: str | None = None
    days: int | None = None
    travelers: int | None = None
    style: str = "moderate"


@dataclass(frozen=True)
class ReconcileResult:
    answer: str
    structured: dict[str, Any]
    trace: list[TraceEvent]
    applied: bool
    reason: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_money(text: str, patterns: tuple[str, ...]) -> tuple[float | None, str | None]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        groups = match.groupdict()
        amount = float(groups["num"].replace(",", ""))
        if groups.get("k"):
            amount *= 1000
        token = groups.get("curr1") or groups.get("curr2") or ""
        return amount, _currency_from_token(token)
    return None, None


def _currency_from_token(token: str) -> str | None:
    if not token:
        return None
    return _normalize_currency(token.replace("US$", "USD")) or None


def _parse_destination(text: str) -> str | None:
    lowered = text.casefold()
    try:
        known = _known_destinations()
    except (OSError, ValueError):
        known = ()
    for name in known:
        if re.search(rf"\b{re.escape(name.casefold())}\b", lowered):
            return name

    for pattern in _DESTINATION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = re.sub(r"^(?:the|a|an)\s+", "", match.group("name"), flags=re.IGNORECASE)
        words = [
            word.strip(" .,;:!?") for word in name.split()
        ]
        words = [word for word in words if word]
        destination: list[str] = []
        for word in words:
            if word.casefold() in _DESTINATION_STOPWORDS:
                break
            destination.append(word)
        if not destination or len(" ".join(destination)) < 2:
            continue
        return " ".join(word[:1].upper() + word[1:] for word in destination)
    return None


def _parse_days(text: str) -> int | None:
    match = _DAYS_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)), 30))
    match = _NIGHTS_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)) + 1, 30))
    return None


def _parse_travelers(text: str) -> int | None:
    match = _TRAVELERS_RE.search(text)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return max(1, min(int(value), 20))


def _parse_style(text: str) -> str:
    lowered = text.casefold()
    if "luxury" in lowered or "premium" in lowered:
        return "luxury"
    budget_style = re.search(
        r"\bbudget(?:[- ](?:style|travel|trip|friendly))\b|\bon a budget\b"
        r"|\b(?:cheap|backpack(?:ing)?)\b",
        lowered,
    )
    return "budget" if budget_style else "moderate"


def parse_query(text: str) -> ParsedQuery:
    budget, budget_currency = _parse_money(text, _BUDGET_PATTERNS)
    cost, cost_currency = _parse_money(text, _COST_PATTERNS)
    return ParsedQuery(
        budget=budget,
        budget_currency=budget_currency,
        cost=cost,
        cost_currency=cost_currency,
        destination=_parse_destination(text),
        days=_parse_days(text),
        travelers=_parse_travelers(text),
        style=_parse_style(text),
    )


def _lookup_rate(source: str, target: str):
    return rates.quote(source, target)


def _money(amount: float) -> str:
    return f"{amount:,.2f}"


def _event(
    step: int,
    event_type: str,
    title: str,
    description: str,
    *,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
    result: str | None = None,
) -> TraceEvent:
    return TraceEvent(
        step=step,
        type=event_type,
        title=title,
        description=description,
        tool=tool,
        arguments=arguments,
        result=result,
        timestamp=_now(),
        status="complete",
    )


def build_deterministic_result(parsed: ParsedQuery, query: str) -> dict[str, Any] | None:
    """Recompute the currency-agent answer from parsed query fields.

    Returns None when the query does not contain a budget plus a usable cost
    source (explicit trip cost or destination), so callers can keep the model's
    best-effort output instead of blocking.
    """
    if parsed.budget is None or parsed.budget <= 0:
        return None

    budget = round(parsed.budget, 2)
    budget_currency = parsed.budget_currency or parsed.cost_currency or "INR"
    if not rates.is_supported(budget_currency):
        return None

    steps: list[dict[str, Any]] = []
    estimate: dict[str, Any] | None = None

    if parsed.cost is not None and parsed.cost > 0:
        total = round(parsed.cost, 2)
        currency = parsed.cost_currency or budget_currency
        if currency != budget_currency:
            quote = _lookup_rate(currency, budget_currency)
            if quote is None:
                return None
            converted = round(total * quote.rate, 2)
            steps.append(
                {
                    "tool": "convert_currency",
                    "arguments": {
                        "amount": total,
                        "from_currency": currency,
                        "to_currency": budget_currency,
                    },
                    "result": (
                        f"Converted {total} {currency} to {converted} {budget_currency} "
                        f"using {quote.description}."
                    ),
                }
            )
            total = converted
        currency = budget_currency
    elif parsed.destination:
        try:
            estimate = compute_trip_estimate(
                parsed.destination,
                parsed.days or 5,
                parsed.travelers or 2,
                parsed.style,
                target_currency=budget_currency,
            )
        except (TypeError, ValueError):
            return None
        total = estimate["total"]
        currency = estimate["currency"]
        if currency != budget_currency:
            return None
        steps.append(
            {
                "tool": "estimate_trip_cost",
                "arguments": {
                    "destination": parsed.destination,
                    "days": estimate["days"],
                    "travelers": estimate["travelers"],
                    "budget_style": estimate["style"],
                    "target_currency": budget_currency,
                },
                "result": format_estimate(estimate),
            }
        )
    else:
        return None

    feasible = total <= budget
    remaining = round(budget - total, 2) if feasible else None
    overage = round(total - budget, 2) if not feasible else None

    if feasible:
        feasibility_text = (
            f"Trip is feasible. Total cost is {total} {currency} and the budget is "
            f"{budget} {currency}. Remaining budget: {remaining} {currency}."
        )
        verdict = f"it is feasible with {_money(remaining)} {currency} remaining"
    else:
        feasibility_text = (
            f"Trip is NOT feasible. Total cost is {total} {currency} but the budget is "
            f"{budget} {currency}. Over budget by {overage} {currency}."
        )
        verdict = f"it is not feasible — you are over budget by {_money(overage)} {currency}"

    steps.append(
        {
            "tool": "check_budget_feasibility",
            "arguments": {
                "total_cost": total,
                "cost_currency": currency,
                "budget": budget,
                "budget_currency": budget_currency,
            },
            "result": feasibility_text,
        }
    )

    answer = (
        f"Deterministic check: the trip costs {_money(total)} {currency} against a budget "
        f"of {_money(budget)} {currency}, so {verdict}. This figure was recalculated with "
        "the local tools because the language model's result was missing or inconsistent."
    )

    trace: list[TraceEvent] = [
        _event(1, "user_request", "User Query", query[:300]),
        _event(
            2,
            "agent_decision",
            "Agent selected next tool",
            "The model's structured result was missing or inconsistent, so the "
            "deterministic guardrail recalculated the answer with the same tools.",
            tool=steps[0]["tool"],
        ),
    ]
    for step in steps:
        trace.append(
            _event(
                len(trace) + 1,
                "tool_call",
                f"Calling tool: {step['tool']}",
                f"Invoking {step['tool']} with the provided arguments.",
                tool=step["tool"],
                arguments=step["arguments"],
            )
        )
        trace.append(
            _event(
                len(trace) + 1,
                "tool_result",
                f"Result: {step['tool']}",
                step["result"],
                tool=step["tool"],
                result=step["result"],
            )
        )
    trace.append(_event(len(trace) + 1, "final_answer", "Agent Final Answer", answer))

    return {
        "answer": answer,
        "feasible": feasible,
        "total_cost": total,
        "budget": budget,
        "remaining": remaining,
        "overage": overage,
        "currency": currency,
        "trace": trace,
    }


def _differs(actual: float | None, expected: float) -> bool:
    if actual is None:
        return True
    return abs(actual - expected) > max(1.0, abs(expected) * 0.01)


def _mismatch_reason(
    structured: dict[str, Any], deterministic: dict[str, Any]
) -> str | None:
    if structured.get("feasible") is None:
        return "missing feasibility"
    if structured.get("total_cost") is None or structured.get("budget") is None:
        return "missing amounts"
    if _differs(structured["budget"], deterministic["budget"]):
        return "budget mismatch"
    if _differs(structured["total_cost"], deterministic["total_cost"]):
        return "cost mismatch"
    expected_currency = deterministic["currency"]
    if structured.get("currency") not in (None, expected_currency):
        return "currency mismatch"
    if structured["feasible"] != (structured["total_cost"] <= structured["budget"]):
        return "inconsistent verdict"
    return None


def reconcile_agent_result(
    query: str,
    answer: str,
    structured: dict[str, Any],
    trace: list[TraceEvent],
) -> ReconcileResult:
    """Replace model output with the deterministic result when it cannot be trusted."""
    try:
        deterministic = build_deterministic_result(parse_query(query), query)
    except Exception:
        logger.warning(
            "Deterministic fallback could not evaluate the query; keeping model output",
            exc_info=True,
        )
        return ReconcileResult(answer, structured, trace, applied=False)
    if deterministic is None:
        return ReconcileResult(answer, structured, trace, applied=False)

    reason = _mismatch_reason(structured, deterministic)
    if reason is None:
        return ReconcileResult(answer, structured, trace, applied=False)

    logger.info(
        "Deterministic fallback replaced agent output (%s) for query %r",
        reason,
        query[:120],
    )
    return ReconcileResult(
        answer=deterministic["answer"],
        structured={
            key: deterministic[key]
            for key in ("feasible", "total_cost", "budget", "remaining", "overage", "currency")
        },
        trace=deterministic["trace"],
        applied=True,
        reason=reason,
    )
