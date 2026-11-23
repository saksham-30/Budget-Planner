"""
LangChain tools for the Travel Budget Planner agent.

These tools are decorated with @tool so LangGraph can bind them to the
model and let the model decide when to call each one.
Docstrings are the primary tool-selection signal for small local models.
"""

import math

from langchain_core.tools import tool

from . import rates
from .rag import DestinationCostGuide

SUPPORTED_CURRENCIES = list(rates.SUPPORTED_CURRENCIES)

CURRENCY_ALIASES: dict[str, str] = {
    "₹": "INR", "RS": "INR", "RS.": "INR", "RUPEES": "INR", "RUPEE": "INR", "INR": "INR",
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR", "EUR": "EUR",
    "$": "USD", "DOLLAR": "USD", "DOLLARS": "USD", "USD": "USD", "US": "USD",
    "£": "GBP", "POUND": "GBP", "POUNDS": "GBP", "GBP": "GBP",
}


def _normalize_currency(curr: str) -> str:
    c = curr.strip()
    return CURRENCY_ALIASES.get(c.upper(), CURRENCY_ALIASES.get(c, c.upper()))


@tool
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """First-step tool: Convert an amount between currencies using the app's current exchange rate.
    MUST be called before check_budget_feasibility whenever trip cost and budget are in different currencies.
    Supported currencies: EUR (€), USD ($), INR (₹), GBP (£).

    Args:
        amount: The monetary amount to convert (must be positive).
        from_currency: Source currency code or symbol (e.g. 'EUR', '€', 'USD', '$', 'INR', '₹', 'GBP', '£').
        to_currency: Target currency code or symbol (e.g. 'INR', '₹', 'EUR', '€', 'USD', '$', 'GBP', '£').

    Returns:
        A human-readable sentence with the converted amount and the rate used.
    """
    from_currency = _normalize_currency(from_currency)
    to_currency = _normalize_currency(to_currency)

    if not math.isfinite(amount) or amount <= 0:
        return (
            f"Validation error: amount must be a finite number greater than zero, got {amount}. "
            "Please provide a positive monetary amount."
        )

    if not rates.is_supported(from_currency):
        return (
            f"Unsupported currency: {from_currency}. "
            f"Supported currencies: {', '.join(SUPPORTED_CURRENCIES)}."
        )

    if not rates.is_supported(to_currency):
        return (
            f"Unsupported currency: {to_currency}. "
            f"Supported currencies: {', '.join(SUPPORTED_CURRENCIES)}."
        )

    if from_currency == to_currency:
        return (
            f"No conversion needed: {amount} {from_currency} is already in "
            f"{to_currency}. Amount remains {amount}."
        )

    quote = rates.quote(from_currency, to_currency)
    if quote is None:
        return (
            f"No exchange rate is available from {from_currency} "
            f"to {to_currency}."
        )

    rate = quote.rate
    converted_amount = amount * rate
    if not math.isfinite(converted_amount):
        return "Validation error: the converted amount is too large to represent."
    converted = round(converted_amount, 2)

    return (
        f"Converted {amount} {from_currency} to {converted} {to_currency} "
        f"using {quote.description}."
    )


def compute_trip_estimate(
    destination: str,
    days: int = 5,
    travelers: int = 2,
    budget_style: str = "moderate",
    target_currency: str = "INR",
) -> dict[str, object]:
    """Deterministic guide-backed estimate shared by the tool and the fallback path."""
    name = destination.strip()
    if not name:
        raise ValueError("destination must not be empty.")

    days = max(1, min(int(days), 30))
    travelers = max(1, min(int(travelers), 20))
    style = str(budget_style).strip().lower()
    if style not in {"budget", "moderate", "luxury"}:
        style = "moderate"

    evidence = DestinationCostGuide().retrieve(name, fallback=True)
    if evidence is None:
        raise ValueError(f"No cost data is available for {name}.")

    daily_rates = {
        category: evidence.estimates[category][style]
        for category in ("hotel", "food", "transport", "activities")
    }
    nights = max(days - 1, 0)
    itemized = {
        "hotel": daily_rates["hotel"] * nights * travelers,
        "food": daily_rates["food"] * days * travelers,
        "transport": daily_rates["transport"] * days * travelers,
        "activities": daily_rates["activities"] * days * travelers,
    }
    guide_total = round(sum(itemized.values()), 2)

    target = _normalize_currency(target_currency)
    total = guide_total
    conversion_note = ""
    if evidence.currency != target:
        if not rates.is_supported(target):
            raise ValueError(f"Unsupported currency: {target}.")
        quote = rates.quote(evidence.currency, target)
        if quote is None:
            conversion_note = (
                f" No exchange rate is available from {evidence.currency} to {target}, "
                f"so the estimate is reported in {evidence.currency}."
            )
            target = evidence.currency
        else:
            total = round(guide_total * quote.rate, 2)
            conversion_note = (
                f" Converted from {guide_total} {evidence.currency} "
                f"using {quote.description}."
            )

    return {
        "destination": evidence.destination,
        "days": days,
        "travelers": travelers,
        "style": style,
        "total": total,
        "currency": target,
        "guide_total": guide_total,
        "guide_currency": evidence.currency,
        "itemized": itemized,
        "conversion_note": conversion_note,
        "is_generic": evidence.is_generic,
        "source_file": evidence.source_file,
        "rate_source": rates.get_snapshot().label,
        "rate_as_of": rates.get_snapshot().as_of,
    }


def format_estimate(estimate: dict[str, object]) -> str:
    """Render a compute_trip_estimate result as the tool/fallback sentence."""
    itemized = estimate["itemized"]
    breakdown = ", ".join(
        f"{category} {round(amount, 2)} {estimate['guide_currency']}"
        for category, amount in itemized.items()
    )
    source = (
        "generic benchmarks (no destination-specific guide entry)"
        if estimate["is_generic"]
        else f"the local cost guide ({estimate['source_file']})"
    )
    return (
        f"Estimated trip cost for {estimate['destination']} is {estimate['total']} "
        f"{estimate['currency']} for {estimate['days']} days and {estimate['travelers']} "
        f"traveler(s) in {estimate['style']} style. Breakdown: {breakdown}."
        f"{estimate['conversion_note']} Source: {source}. "
        f"Use {estimate['total']} {estimate['currency']} as total_cost in "
        "check_budget_feasibility."
    )


@tool
def estimate_trip_cost(
    destination: str,
    days: int = 5,
    travelers: int = 2,
    budget_style: str = "moderate",
    target_currency: str = "INR",
) -> str:
    """Estimate a total trip cost from the local illustrative cost guide.
    Only call this when the user names a destination but does NOT state an explicit trip cost.
    If the user already gave a trip cost (e.g. "trip cost is 900 EUR"), do NOT call this tool.
    Call this BEFORE check_budget_feasibility and pass its estimate as total_cost.

    Args:
        destination: City or region the user wants to visit (e.g. 'Manali', 'Goa', 'Paris').
        days: Number of days for the trip (default 5).
        travelers: Number of travelers (default 2).
        budget_style: One of 'budget', 'moderate', or 'luxury' (default 'moderate').
        target_currency: Currency for the estimate, normally the user's budget currency (default 'INR').

    Returns:
        The estimated total with a per-category breakdown and the data source.
    """
    try:
        estimate = compute_trip_estimate(
            destination, days, travelers, budget_style, target_currency
        )
    except (TypeError, ValueError) as exc:
        return f"Validation error: {exc}"
    return format_estimate(estimate)


@tool
def check_budget_feasibility(total_cost: float, cost_currency: str, budget: float, budget_currency: str) -> str:
    """Compare a total trip cost against a budget and report whether the trip is feasible.
    Currencies MUST match before calling this tool. If they don't match, you must use convert_currency first.

    Args:
        total_cost: The total cost of the trip.
        cost_currency: The currency of the total cost (e.g. 'EUR', 'INR').
        budget: The available budget.
        budget_currency: The currency of the budget (e.g. 'EUR', 'INR').

    Returns:
        A human-readable sentence stating feasibility, or an error if currencies don't match.
    """
    cost_curr = _normalize_currency(cost_currency)
    budg_curr = _normalize_currency(budget_currency)

    if (
        not math.isfinite(total_cost)
        or not math.isfinite(budget)
        or total_cost < 0
        or budget < 0
    ):
        return (
            f"Validation error: total_cost ({total_cost}) and budget ({budget}) "
            "must be finite, non-negative amounts."
        )

    unsupported = [
        currency for currency in (cost_curr, budg_curr)
        if not rates.is_supported(currency)
    ]
    if unsupported:
        return (
            f"Unsupported currency: {unsupported[0]}. "
            f"Supported currencies: {', '.join(SUPPORTED_CURRENCIES)}."
        )

    if cost_curr != budg_curr:
        return (
            f"Error: Currencies do not match (Cost: {cost_curr}, Budget: {budg_curr}). "
            "You MUST call `convert_currency` first to convert the trip cost to the budget currency, "
            "and then call this tool again with the converted amount and matching currencies."
        )

    if total_cost <= budget:
        remaining = round(budget - total_cost, 2)
        return (
            f"Trip is feasible. Total cost is {total_cost} {cost_curr} and the budget is {budget} {budg_curr}. "
            f"Remaining budget: {remaining} {budg_curr}."
        )
    else:
        overage = round(total_cost - budget, 2)
        return (
            f"Trip is NOT feasible. Total cost is {total_cost} {cost_curr} but the budget is {budget} {budg_curr}. "
            f"Over budget by {overage} {budg_curr}."
        )
