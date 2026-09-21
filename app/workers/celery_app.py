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
"""
from __future__ import annotations

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


def _configure_beat_schedule() -> None:
    scheduler_cfg = settings.scheduler_config()
    if not scheduler_cfg.get("enabled", False):
        celery_app.conf.beat_schedule = {}
        return

    cron_expression = scheduler_cfg.get("research_job_cron", "0 8 * * 1")
    celery_app.conf.beat_schedule = {
        "weekly-research-job": {
            "task": "app.workers.tasks.run_scheduled_research_job",
            "schedule": _crontab_from_string(cron_expression),
        }
    }


_configure_beat_schedule()

# Registers the two real tasks against this app -- imported for its side
# effect (the @celery_app.task decorators), same reason
# `app/models/__init__.py` imports every model module for Alembic's
# metadata. Import at the bottom: app/workers/tasks.py imports
# `celery_app` from this module, so importing tasks any earlier would be
# circular.
from app.workers import tasks  # noqa: F401
