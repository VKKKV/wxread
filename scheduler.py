import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def parse_cron_schedule(schedule, timezone="Asia/Shanghai"):
    """Pass five fields directly to CronTrigger, preserving upstream semantics."""
    fields = schedule.strip().split()
    if len(fields) != 5:
        raise ValueError(f"无效的 cron 表达式，必须包含 5 个字段: {schedule}")
    return CronTrigger(
        minute=fields[0],
        hour=fields[1],
        day=fields[2],
        month=fields[3],
        day_of_week=fields[4],
        timezone=timezone,
    )


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
    next_run = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    if next_run is None:
        raise ValueError("CRON_SCHEDULE has no future fire time")
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_job,
        trigger,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logging.info("调度器已启动。下一次任务执行时间: %s", next_run)
    scheduler.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.critical("启动调度器失败: %s", exc)
        sys.exit(1)
