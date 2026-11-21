"""When Swedish SE3 electricity is cheapest over the next day.

Reads the public Elpriset just nu JSON files. No API key.
Prices are spot prices in SEK/kWh, excluding VAT and grid fees.

Exit 0: the price is cheap enough, the agent may claim and redraw.
Exit 2: the price is high, or the next day is the plan and the agent must wait.
Exit 1: the price could not be read. Do not redraw.
"""

from __future__ import annotations

import http.client
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo("Europe/Stockholm")
AREA = "SE3"
SOURCE = "https://www.elprisetjustnu.se/api/v1/prices"
# Wait when the current price is more than 25% above the cheapest hour.
HIGH_RATIO = 1.25
# When the cheap hour is at or below zero, wait if now is more than this many SEK/kWh above it.
HIGH_FLOOR_GAP = 0.05
HOUR_MINUTES = 60


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    sek: float

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


def price_url(day) -> str:
    return f"{SOURCE}/{day.year}/{day.month:02d}-{day.day:02d}_{AREA}.json"


def fetch_prices(day) -> list[Slot] | None:
    request = urllib.request.Request(price_url(day), headers={"User-Agent": "simcity-ai-karta/se3-price"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"SE3 price request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("SE3 price request failed") from exc
    # A read timeout and a dropped connection mean the price could not be read.
    except (TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        raise RuntimeError(f"SE3 price request failed ({exc.__class__.__name__})") from exc
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"SE3 price request failed with HTTP {status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SE3 price response was not JSON") from exc
    return parse_slots(payload)


def parse_slots(payload) -> list[Slot]:
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("SE3 price response was empty")
    slots = []
    for row in payload:
        try:
            start = datetime.fromisoformat(row["time_start"])
            end = datetime.fromisoformat(row["time_end"])
            sek = float(row["SEK_per_kWh"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("SE3 price row was missing time or SEK_per_kWh") from exc
        if start.tzinfo is None or end.tzinfo is None:
            raise RuntimeError("SE3 price timestamps must include a timezone offset")
        slots.append(Slot(start=start, end=end, sek=sek))
    slots.sort(key=lambda slot: slot.start)
    return slots


def price_at(slots: list[Slot], now: datetime) -> float | None:
    for slot in slots:
        if slot.start <= now < slot.end:
            return slot.sek
    return None


def cheapest_slot(slots: list[Slot]) -> Slot:
    return min(slots, key=lambda slot: (slot.sek, slot.start))


def cheapest_hour(slots: list[Slot], minutes: int = HOUR_MINUTES) -> dict | None:
    best = None
    for start_index, _slot in enumerate(slots):
        total = 0.0
        duration = 0.0
        end_index = start_index
        while end_index < len(slots) and duration < minutes - 1e-6:
            if end_index > start_index:
                gap = (slots[end_index].start - slots[end_index - 1].end).total_seconds()
                if gap > 90:
                    break
            total += slots[end_index].sek * slots[end_index].minutes
            duration += slots[end_index].minutes
            end_index += 1
        if duration < minutes - 1e-6:
            continue
        average = total / duration
        window = {
            "start": slots[start_index].start,
            "end": slots[end_index - 1].end,
            "avg": average,
        }
        if best is None or average < best["avg"] - 1e-12 or (
            abs(average - best["avg"]) <= 1e-12 and window["start"] < best["start"]
        ):
            best = window
    return best


def price_is_high(now_price: float, cheap: float) -> bool:
    if cheap <= 0:
        return now_price > cheap + HIGH_FLOOR_GAP
    return now_price > cheap * HIGH_RATIO


def _remaining(slots: list[Slot] | None, now: datetime) -> list[Slot]:
    if not slots:
        return []
    return [slot for slot in slots if slot.end > now]


def evaluate(now: datetime, today: list[Slot] | None, next_day: list[Slot] | None, next_date) -> dict:
    """Decide whether an agent should redraw now.

    The next day is the next Europe/Stockholm calendar date. When that file
    is published, its cheapest hour is the time this script reports. When it
    is not published yet, the rest of today is the fallback and the report
    says so.
    """
    now = now.astimezone(STOCKHOLM)
    published = next_day is not None
    if published:
        horizon_slots = next_day
        horizon = "next-day"
    else:
        horizon_slots = _remaining(today, now)
        horizon = "rest-of-today"

    report = {
        "area": AREA,
        "next_day": next_date.isoformat(),
        "next_day_published": "yes" if published else "no",
        "horizon": horizon,
        "now": now.isoformat(),
        "source": price_url(next_date) if published else price_url(now.date()),
        "vat": "excluded",
        "rule": (
            "WAIT when the current SE3 price is above 1.25 times the cheapest hour, "
            "or 0.05 SEK/kWh above it when that hour is at or below zero. RUN during that hour."
        ),
    }

    if not horizon_slots:
        report.update(
            {
                "decision": "WAIT",
                "exit_code": 2,
                "action": (
                    "No SE3 prices are available for the horizon. "
                    "Keep your lock and run rutor redraw --wait-for-cheap again after tomorrow's prices "
                    "are published, usually after 13:00 Europe/Stockholm."
                ),
            }
        )
        return report

    quarter = cheapest_slot(horizon_slots)
    hour = cheapest_hour(horizon_slots) or {
        "start": quarter.start,
        "end": quarter.end,
        "avg": quarter.sek,
    }
    now_price = price_at(next_day or [], now)
    if now_price is None:
        now_price = price_at(today or [], now)

    report.update(
        {
            "cheapest_quarter_start": quarter.start.isoformat(),
            "cheapest_quarter_end": quarter.end.isoformat(),
            "cheapest_quarter_sek_per_kwh": f"{quarter.sek:.5f}",
            "cheapest_hour_start": hour["start"].isoformat(),
            "cheapest_hour_end": hour["end"].isoformat(),
            "cheapest_hour_sek_per_kwh": f"{hour['avg']:.5f}",
        }
    )
    if now_price is None:
        report["now_sek_per_kwh"] = "unknown"
    else:
        report["now_sek_per_kwh"] = f"{now_price:.5f}"

    inside = hour["start"] <= now < hour["end"]
    high = now_price is None or price_is_high(now_price, hour["avg"])
    if inside or not high:
        if not published:
            why = "The next day's SE3 prices are not published yet. The rest of today is cheap enough."
        elif inside:
            why = "The current SE3 quarter is inside the cheapest hour."
        else:
            why = "The current SE3 price is close to the cheapest hour."
        report["decision"] = "RUN"
        report["exit_code"] = 0
        report["action"] = f"{why} Run the redraw now."
        return report

    when = hour["start"].astimezone(STOCKHOLM).strftime("%Y-%m-%d %H:%M %Z")
    if not published:
        extra = " The next day's file is not published yet; this uses the rest of today."
    else:
        extra = ""
    report["decision"] = "WAIT"
    report["exit_code"] = 2
    report["action"] = (
        f"The SE3 price is high. Keep your lock and run rutor redraw --wait-for-cheap again after {when}.{extra}"
    )
    return report


def render(report: dict) -> str:
    order = [
        "area",
        "source",
        "vat",
        "next_day",
        "next_day_published",
        "horizon",
        "now",
        "now_sek_per_kwh",
        "rule",
        "cheapest_quarter_start",
        "cheapest_quarter_end",
        "cheapest_quarter_sek_per_kwh",
        "cheapest_hour_start",
        "cheapest_hour_end",
        "cheapest_hour_sek_per_kwh",
        "decision",
        "action",
    ]
    lines = [f"{key}: {report[key]}" for key in order if key in report]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, fetch=fetch_prices, clock=None) -> int:
    del argv  # the script takes no arguments; a flag cannot talk it into RUN
    now = clock() if clock else datetime.now(STOCKHOLM)
    now = now.astimezone(STOCKHOLM)
    try:
        today = fetch(now.date())
        next_date = now.date() + timedelta(days=1)
        tomorrow = fetch(next_date)
        report = evaluate(now, today, tomorrow, next_date)
    except Exception as exc:
        print("area: SE3", flush=True)
        print("decision: WAIT", flush=True)
        print(
            "action: The public SE3 price could not be read. Keep your lock and try again later, "
            "or run rutor redraw without --wait-for-cheap. "
            f"({exc.__class__.__name__})",
            flush=True,
        )
        return 1
    sys.stdout.write(render(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
