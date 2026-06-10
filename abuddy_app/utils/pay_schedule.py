from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta


DEFAULT_PAY_INTERVAL_DAYS = 14
DEFAULT_KNOWN_PAYDAY = date(2026, 6, 12)


def parse_date(value: object, fallback: date | None = None) -> date:
    """Parse ISO date values safely and fall back when parsing fails."""
    if isinstance(value, date):
        return value
    if value is None:
        return fallback or DEFAULT_KNOWN_PAYDAY
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback or DEFAULT_KNOWN_PAYDAY


def clamp_day(year: int, month: int, day: int) -> date:
    """Clamp a day-of-month into the valid range for a given month."""
    last_day = monthrange(year, month)[1]
    return date(year, month, max(1, min(int(day), last_day)))


def month_end(reference_date: date) -> date:
    """Return the last calendar day for the month containing reference_date."""
    return clamp_day(reference_date.year, reference_date.month, 31)


def generate_future_paydays(
    anchor_payday: date,
    interval_days: int = DEFAULT_PAY_INTERVAL_DAYS,
    count: int = 24,
    start_date: date | None = None,
) -> list[date]:
    """Generate future payday dates from a known anchor date."""
    safe_interval = max(1, int(interval_days or DEFAULT_PAY_INTERVAL_DAYS))
    current = anchor_payday
    if start_date is not None:
        while current < start_date:
            current += timedelta(days=safe_interval)
    return [current + timedelta(days=safe_interval * index) for index in range(count)]


def get_next_payday(
    reference_date: date,
    anchor_payday: date,
    interval_days: int = DEFAULT_PAY_INTERVAL_DAYS,
) -> date:
    """Return the next payday on or after the reference date."""
    safe_interval = max(1, int(interval_days or DEFAULT_PAY_INTERVAL_DAYS))
    current = anchor_payday
    while current < reference_date:
        current += timedelta(days=safe_interval)
    return current


def get_current_pay_period(
    reference_date: date,
    anchor_payday: date,
    interval_days: int = DEFAULT_PAY_INTERVAL_DAYS,
) -> tuple[date, date]:
    """Return the pay period containing reference_date."""
    safe_interval = max(1, int(interval_days or DEFAULT_PAY_INTERVAL_DAYS))
    current = anchor_payday
    while current + timedelta(days=safe_interval) <= reference_date:
        current += timedelta(days=safe_interval)
    while current > reference_date:
        current -= timedelta(days=safe_interval)
    return current, current + timedelta(days=safe_interval - 1)


def get_paydays_between(
    start_date: date,
    end_date: date,
    anchor_payday: date,
    interval_days: int = DEFAULT_PAY_INTERVAL_DAYS,
) -> list[date]:
    """Return paydays between start_date and end_date inclusive."""
    safe_interval = max(1, int(interval_days or DEFAULT_PAY_INTERVAL_DAYS))
    current = anchor_payday
    while current < start_date:
        current += timedelta(days=safe_interval)

    paydays: list[date] = []
    while current <= end_date:
        paydays.append(current)
        current += timedelta(days=safe_interval)
    return paydays


def get_remaining_paydays_in_month(
    reference_date: date,
    anchor_payday: date,
    interval_days: int = DEFAULT_PAY_INTERVAL_DAYS,
) -> list[date]:
    """Return future paydays after reference_date through month end."""
    return get_paydays_between(
        start_date=reference_date + timedelta(days=1),
        end_date=month_end(reference_date),
        anchor_payday=anchor_payday,
        interval_days=interval_days,
    )


def days_until_payday(reference_date: date, next_payday: date) -> int:
    """Return the day difference from reference_date to next_payday."""
    return max(0, (next_payday - reference_date).days)