import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def _parse_field(text, minimum, maximum, names=None, sunday=False):
    """Parse a five-field Unix cron value to an APScheduler-compatible list."""
    names = {name.lower(): value for name, value in (names or {}).items()}
    wildcard = text.startswith("*")
    values = set()

    def number(token):
        key = token.lower()
        if key in names:
            value = names[key]
        elif re.fullmatch(r"\d+", token):
            value = int(token)
        else:
            raise ValueError(f"invalid cron field value: {token!r}")
        if not minimum <= value <= maximum:
            raise ValueError(f"cron value {token!r} outside {minimum}..{maximum}")
        return value

    for item in text.split(","):
        if not item:
            raise ValueError("empty item in cron field")
        if "/" in item:
            if item.count("/") != 1:
                raise ValueError(f"unsupported cron syntax: {item!r}")
            base, step_text = item.split("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"invalid cron step: {step_text!r}")
            step = int(step_text)
            if base == "*":
                start, end = minimum, maximum
            elif "-" in base:
                if base.count("-") != 1:
                    raise ValueError(f"unsupported cron syntax: {item!r}")
                left, right = base.split("-")
                start, end = number(left), number(right)
            else:
                start, end = number(base), maximum
            if start > end:
                raise ValueError(f"descending cron range: {item!r}")
            values.update(range(start, end + 1, step))
        elif item == "*":
            values.update(range(minimum, maximum + 1))
        elif "-" in item:
            if item.count("-") != 1:
                raise ValueError(f"unsupported cron syntax: {item!r}")
            left, right = item.split("-")
            start, end = number(left), number(right)
            if start > end:
                raise ValueError(f"descending cron range: {item!r}")
            values.update(range(start, end + 1))
        else:
            values.add(number(item))

    # APScheduler numbers weekdays Monday=0; Unix cron Sunday=0.
    if sunday:
        values = {(value - 1) % 7 for value in values}
    return sorted(values), wildcard


def parse_cron_schedule(schedule, timezone="Asia/Shanghai"):
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError(f"invalid cron expression: expected 5 fields, got {len(fields)}")
    minute, _ = _parse_field(fields[0], 0, 59)
    hour, _ = _parse_field(fields[1], 0, 23)
    day, day_wild = _parse_field(fields[2], 1, 31)
    month, _ = _parse_field(fields[3], 1, 12, {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    })
    weekday, weekday_wild = _parse_field(fields[4], 0, 7, {
        "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
    }, sunday=True)
    # CronTrigger accepts lists as comma-separated strings (not Python lists).
    minute, hour, month = (",".join(map(str, values)) for values in (minute, hour, month))
    day, weekday = ",".join(map(str, day)), ",".join(map(str, weekday))
    common = dict(minute=minute, hour=hour, month=month, timezone=timezone)
    if not day_wild and not weekday_wild:
        triggers = [
            CronTrigger(**common, day=day, day_of_week="*"),
            CronTrigger(**common, day="*", day_of_week=weekday),
        ]
        return OrTrigger(triggers)
    return CronTrigger(**common, day=day, day_of_week=weekday)


def _positive_int_env(name, default):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def run_job():
    logging.info("开始执行定时阅读任务...")
    script_dir = Path(__file__).resolve().parent
    try:
        read_num = _positive_int_env("READ_NUM", 40)
        timeout = _positive_int_env("JOB_TIMEOUT_SECONDS", max(3600, read_num * 90 + 900))
        subprocess.run(
            [sys.executable, str(script_dir / "main.py")],
            cwd=str(script_dir),
            capture_output=False,
            check=True,
            timeout=timeout,
        )
        logging.info("定时阅读任务执行成功。")
    except subprocess.TimeoutExpired:
        logging.error("定时任务超时，已终止（timeout seconds=%s）。", os.getenv("JOB_TIMEOUT_SECONDS") or timeout)
    except subprocess.CalledProcessError as exc:
        logging.error("定时任务执行失败，退出码：%s", exc.returncode)
    except Exception as exc:
        logging.error("运行定时任务时发生本地异常（%s）。", type(exc).__name__)


def main():
    read_num = _positive_int_env("READ_NUM", 40)
    _positive_int_env("JOB_TIMEOUT_SECONDS", max(3600, read_num * 90 + 900))
    cron_schedule = os.getenv("CRON_SCHEDULE", "0 1 * * *")
    timezone = os.getenv("TZ", "Asia/Shanghai")
    logging.info("使用时区: %s", timezone)
    logging.info("解析定时规则 (CRON_SCHEDULE): %s", cron_schedule)
    trigger = parse_cron_schedule(cron_schedule, timezone)
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_job,
        trigger,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    next_run = trigger.get_next_fire_time(None, datetime.now(scheduler.timezone))
    logging.info("调度器已启动。下一次任务执行时间: %s", next_run)
    scheduler.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.critical("启动调度器失败: %s", exc)
        sys.exit(1)
