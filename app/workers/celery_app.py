"""
Celery application — Milestone 14 ("Workers + scheduler").

`pyproject.toml` has declared `celery>=5.4`/`redis>=5.0` as dependencies
since Milestone 1; `Makefile`'s `run-worker` target
(`celery -A app.workers.celery_app worker --loglevel=info`) has pointed at
this exact module path since the same commit. Both were unused until now
-- `docker-compose.yml`'s own comment says it plainly: "Deliberately NOT
here: a `worker`/celery service... That service is Milestone 14's to add,
once there's a real task to run." `app/workers/tasks.py` is that real
task; this module is the Celery app it's registered against.

Broker AND result backend are both Redis (`settings.redis_url`) -- no
new infra beyond what `.env`/`docker-compose.yml` already provision.
Task/result serialization is JSON, not Celery's pickle default: every
argument and return value in `app/workers/tasks.py` is already
JSON-safe (str/int/None), and JSON avoids pickle's arbitrary-code-
execution surface on a queue this process doesn't fully control end to
end -- not a hardening exercise for its own sake, just not taking on a
new sharp edge for no reason.

**Beat schedule, wired for real, not hardcoded**: `scheduler.enabled`
and `scheduler.research_job_cron` come from `Settings.scheduler_config()`
(config/default.yaml) -- both `false` by default, so `beat_schedule` is
empty unless a human turns the flag on. `research_job_cron`'s five
fields map positionally onto `celery.schedules.crontab`'s
minute/hour/day_of_month/month_of_year/day_of_week -- standard cron
field order, same as the string `config/default.yaml` already contains
("0 8 * * 1" = Monday 08:00 UTC). No new cron-parsing dependency: Celery
already accepts each field as the same range/step/list syntax raw cron
uses, so a plain `str.split()` is enough -- confirmed against Celery's
own `crontab` docs, not assumed.

**Weekly Intelligence Scan schedule (feature, 2026-09-21), added
alongside the above -- a second, independent `beat_schedule` entry, not
a replacement.** `weekly_scan.enabled`/`day`/`time`/`timezone` come from
`Settings.weekly_scan_config()` (config/default.yaml's `weekly_scan:`
section); `enabled: false` by default, per that config's own comment --
this only fires after the first manual `dinamik scan weekly` run has
been validated by a human (spec sections 40/45), never automatically.

This app's global Celery setting is `timezone="UTC", enable_utc=True`
(set above, for the pre-existing `research_job_cron` schedule) --
`celery.schedules.crontab` has no per-entry timezone parameter, so a
`weekly_scan.timezone: "Europe/Rome"` config value has to be converted
to a UTC hour/minute *before* being handed to `crontab(...)`, not passed
through as-is. `_weekly_scan_crontab()` below does that conversion once,
at schedule-build time (`_configure_beat_schedule()`, called at import),
using the stdlib `zoneinfo` module (no new dependency -- Python's own
IANA tz database) against the *next* matching weekday/time in Rome's
local time, so the correct DST offset (CET UTC+1 or CEST UTC+2) for that
specific date is used rather than a guess.

**Known, documented limitation, not silently swallowed**: because the
conversion happens once at process start (not re-evaluated on every
Beat tick), a Beat process that stays running across a DST transition
(the last Sunday of March or October) keeps firing at the
already-computed UTC time until the process is next restarted -- the
scan will land an hour off from "06:00 Europe/Rome" for any fire that
happens between the DST change and the next deploy/restart. Given
Celery Beat's own scheduler has no built-in per-entry timezone support
and this project's explicit brief was "no large scheduling framework"
(spec section 12), this is the accepted trade-off for a background job
where the exact hour genuinely doesn't matter -- not something a
correctly-scoped fix would silently paper over. A deploy that restarts
Beat at least once a season (routine for this project's cadence) keeps
the drift bounded to at most one DST transition's worth.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("dinamik", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def _crontab_from_string(expression: str) -> crontab:
    minute, hour, day_of_month, month_of_year, day_of_week = expression.split()
    return crontab(
        minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month_of_year, day_of_week=day_of_week
    )


_ISO_WEEKDAY_BY_NAME = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 7,
}


def _weekly_scan_crontab(day: str, time_str: str, tz_name: str) -> crontab:
    """Converts a local day/time/timezone (config/default.yaml's
    `weekly_scan:` shape) into a UTC crontab -- see this module's
    docstring for why the conversion, and its DST limitation, exist."""
    hour_str, minute_str = time_str.split(":")
    target_iso_weekday = _ISO_WEEKDAY_BY_NAME[day.strip().lower()]

    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    days_ahead = (target_iso_weekday - now_local.isoweekday()) % 7
    target_date = (now_local + timedelta(days=days_ahead)).date()
    local_dt = datetime(
        target_date.year, target_date.month, target_date.day, int(hour_str), int(minute_str), tzinfo=tz
    )
    utc_dt = local_dt.astimezone(UTC)
    cron_day_of_week = utc_dt.isoweekday() % 7  # cron: Sunday=0..Saturday=6; isoweekday Sunday=7 -> 0
    return crontab(minute=str(utc_dt.minute), hour=str(utc_dt.hour), day_of_week=str(cron_day_of_week))


def _configure_beat_schedule() -> None:
    schedule: dict[str, dict[str, Any]] = {}

    scheduler_cfg = settings.scheduler_config()
    if scheduler_cfg.get("enabled", False):
        cron_expression = scheduler_cfg.get("research_job_cron", "0 8 * * 1")
        schedule["weekly-research-job"] = {
            "task": "app.workers.tasks.run_scheduled_research_job",
            "schedule": _crontab_from_string(cron_expression),
        }

    weekly_scan_cfg = settings.weekly_scan_config()
    if weekly_scan_cfg.get("enabled", False):
        schedule["weekly-intelligence-scan"] = {
            "task": "app.workers.tasks.run_weekly_scan_task",
            "schedule": _weekly_scan_crontab(
                weekly_scan_cfg.get("day", "sunday"),
                weekly_scan_cfg.get("time", "06:00"),
                weekly_scan_cfg.get("timezone", "Europe/Rome"),
            ),
        }

    celery_app.conf.beat_schedule = schedule


_configure_beat_schedule()

# Registers the two real tasks against this app -- imported for its side
# effect (the @celery_app.task decorators), same reason
# `app/models/__init__.py` imports every model module for Alembic's
# metadata. Import at the bottom: app/workers/tasks.py imports
# `celery_app` from this module, so importing tasks any earlier would be
# circular.
from app.workers import tasks  # noqa: F401
