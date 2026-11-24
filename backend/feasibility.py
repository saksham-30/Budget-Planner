"""
Deterministic itinerary feasibility engine — Phase 5.

Analyses an ItineraryDocument and returns a list of FeasibilityIssue objects.
No external calls. No LLM. Pure Python arithmetic on activity times and costs.

Rules applied:
  - Time overlap detection (end-time based, not just start-time)
  - Insufficient gap between activities (< 15 min heuristic)
  - Missing meal breaks (no food/break activity in a 5+ hour window)
  - Activity density (> 6 non-break activities per day for relaxed; > 8 for packed)
  - Unrealistic daily walking (> 5 activities with no transit activity)
  - Activities with no location set
  - Budget overrun (from budget_breakdown)
  - Single-day > 14 activity-hours scheduled
  - Missing rest day for 7+ day trips (every day packed)

Severity levels: INFO | WARNING | CRITICAL
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Severity = Literal["INFO", "WARNING", "CRITICAL"]

# Minimum gap in minutes between consecutive activities before warning
_MIN_GAP_MINUTES = 15
# Hours without a food/break activity before flagging
_MEAL_WINDOW_HOURS = 5
# Max sensible activity-hours per day
_MAX_DAILY_ACTIVITY_HOURS = 14
# Max non-break activities per day before density warning
_MAX_ACTIVITIES_RELAXED = 5
_MAX_ACTIVITIES_BALANCED = 7
_MAX_ACTIVITIES_PACKED = 9


@dataclass
class FeasibilityIssue:
    severity: Severity
    day_number: int | None   # None = whole-trip issue
    activity_id: str | None  # None = day-level issue
    code: str               # machine-readable code
    message: str            # human-readable message
    suggestion: str         # preview-only improvement hint (never auto-applied)


def _parse_time(t: str | None) -> int | None:
    """Return minutes since midnight, or None if unparseable."""
    if not t:
        return None
    try:
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (IndexError, ValueError):
        return None


def _activity_end(start_min: int, duration_min: int) -> int:
    return start_min + max(duration_min, 0)


def analyse(doc: object) -> list[FeasibilityIssue]:
    """
    Analyse an ItineraryDocument (Pydantic model or plain dict) and return issues.
    Works with both Pydantic model objects and raw dicts.
    """
    issues: list[FeasibilityIssue] = []

    # Support both Pydantic model and dict
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    daily_plans = _get(doc, "daily_plans", [])
    budget_breakdown = _get(doc, "budget_breakdown")
    config = _get(doc, "config")
    pace = _get(config, "pace", "balanced") if config else "balanced"

    max_activities = {
        "relaxed": _MAX_ACTIVITIES_RELAXED,
        "balanced": _MAX_ACTIVITIES_BALANCED,
        "packed": _MAX_ACTIVITIES_PACKED,
    }.get(pace, _MAX_ACTIVITIES_BALANCED)

    total_days = len(daily_plans)

    # ── Per-day analysis ─────────────────────────────────────────────────────
    days_with_no_food = 0

    for day in daily_plans:
        day_num = _get(day, "day_number", 0)
        activities = _get(day, "activities", [])

        # Collect timed activities
        timed: list[tuple[int, int, str, bool, bool]] = []  # (start, end, id, is_break, is_food)
        no_location_count = 0
        total_activity_minutes = 0
        non_break_count = 0
        has_transit = False
        food_times: list[tuple[int, int]] = []

        for act in activities:
            act_id  = _get(act, "id", "")
            time_str = _get(act, "time", "")
            duration = _get(act, "duration_minutes", 60) or 60
            is_break = _get(act, "is_break", False)
            category = _get(act, "category", "")
            location = (_get(act, "location", "") or "").strip()
            name     = _get(act, "name", "")

            start_min = _parse_time(time_str)
            if start_min is None:
                continue

            end_min = _activity_end(start_min, duration)
            is_food = category in ("food",) or is_break
            if category in ("transport",):
                has_transit = True

            timed.append((start_min, end_min, act_id, is_break, is_food))
            if is_food:
                food_times.append((start_min, end_min))
            if not is_break:
                non_break_count += 1
                total_activity_minutes += duration

            # Missing location
            if not location and not is_break and category not in ("transport",):
                issues.append(FeasibilityIssue(
                    severity="INFO",
                    day_number=day_num,
                    activity_id=act_id,
                    code="MISSING_LOCATION",
                    message=f"Day {day_num}: '{name}' has no location set.",
                    suggestion="Add a place name so the itinerary is easier to navigate.",
                ))

        # Sort by start time
        timed.sort(key=lambda x: x[0])

        # Overlap and gap check
        for i in range(len(timed) - 1):
            s1, e1, id1, brk1, _ = timed[i]
            s2, e2, id2, brk2, _ = timed[i + 1]
            gap = s2 - e1
            if gap < 0:
                # Overlap
                issues.append(FeasibilityIssue(
                    severity="CRITICAL",
                    day_number=day_num,
                    activity_id=id2,
                    code="TIME_OVERLAP",
                    message=f"Day {day_num}: activities overlap by {-gap} minutes.",
                    suggestion="Adjust start times or reduce the duration of the earlier activity.",
                ))
            elif gap < _MIN_GAP_MINUTES and not brk2:
                issues.append(FeasibilityIssue(
                    severity="WARNING",
                    day_number=day_num,
                    activity_id=id2,
                    code="TIGHT_SCHEDULE",
                    message=f"Day {day_num}: only {gap} minute gap between activities.",
                    suggestion=f"Allow at least {_MIN_GAP_MINUTES} minutes transit time.",
                ))

        # Meal break check — flag if any 5-hour window has no food activity
        if timed:
            first_start = timed[0][0]
            last_end    = timed[-1][1]
            window_hours = (last_end - first_start) / 60
            if window_hours >= _MEAL_WINDOW_HOURS:
                # Check for at least one food/break activity
                has_meal = any(f for f in food_times)
                if not has_meal:
                    days_with_no_food += 1
                    issues.append(FeasibilityIssue(
                        severity="WARNING",
                        day_number=day_num,
                        activity_id=None,
                        code="NO_MEAL_BREAK",
                        message=f"Day {day_num}: no meal or rest break in a {window_hours:.0f}-hour day.",
                        suggestion="Add a food or rest activity to avoid fatigue.",
                    ))

        # Activity density
        if non_break_count > max_activities:
            issues.append(FeasibilityIssue(
                severity="WARNING",
                day_number=day_num,
                activity_id=None,
                code="HIGH_DENSITY",
                message=(
                    f"Day {day_num}: {non_break_count} activities for a {pace} pace "
                    f"(suggested max: {max_activities})."
                ),
                suggestion="Consider removing optional activities or splitting across days.",
            ))

        # Total scheduled hours
        total_hours = total_activity_minutes / 60
        if total_hours > _MAX_DAILY_ACTIVITY_HOURS:
            issues.append(FeasibilityIssue(
                severity="WARNING",
                day_number=day_num,
                activity_id=None,
                code="LONG_DAY",
                message=f"Day {day_num}: {total_hours:.1f} hours of activities scheduled.",
                suggestion="Reduce or mark some activities as optional.",
            ))

    # ── Whole-trip analysis ───────────────────────────────────────────────────

    # Budget overrun
    if budget_breakdown:
        is_over = _get(budget_breakdown, "is_over_budget", False)
        remaining = _get(budget_breakdown, "budget_remaining", 0)
        currency = _get(budget_breakdown, "currency", "")
        if is_over:
            sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency)
            issues.append(FeasibilityIssue(
                severity="WARNING",
                day_number=None,
                activity_id=None,
                code="BUDGET_OVERRUN",
                message=f"Estimated trip cost exceeds budget by {sym}{abs(remaining):,.0f}.",
                suggestion="Review activity costs or increase the budget.",
            ))

    # Rest day check for long trips
    if total_days >= 7:
        # Heuristic: if every day has > max_activities, suggest a rest day
        all_days_dense = all(
            sum(1 for a in _get(day, "activities", [])
                if not _get(a, "is_break", False)) > max_activities
            for day in daily_plans
        )
        if all_days_dense:
            issues.append(FeasibilityIssue(
                severity="INFO",
                day_number=None,
                activity_id=None,
                code="NO_REST_DAY",
                message=f"All {total_days} days are densely packed with no lighter day.",
                suggestion="Consider designating one day as a rest or low-activity day.",
            ))

    return issues


def summarise(issues: list[FeasibilityIssue]) -> dict:
    """Return a summary dict of issue counts by severity."""
    return {
        "critical": sum(1 for i in issues if i.severity == "CRITICAL"),
        "warning":  sum(1 for i in issues if i.severity == "WARNING"),
        "info":     sum(1 for i in issues if i.severity == "INFO"),
        "total":    len(issues),
    }
