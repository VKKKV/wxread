import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import scheduler


class CronParsingTests(unittest.TestCase):
    tz = "UTC"

    def test_default_schedule_retained(self):
        trigger = scheduler.parse_cron_schedule("0 1 * * *", self.tz)
        now = datetime(2026, 1, 1, 0, 0, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(trigger.get_next_fire_time(None, now), datetime(2026, 1, 1, 1, 0, tzinfo=ZoneInfo(self.tz)))

    def test_unix_sunday_zero_and_seven(self):
        for weekday in ("0", "7", "sun", "SUN"):
            with self.subTest(weekday=weekday):
                trigger = scheduler.parse_cron_schedule(f"0 0 * * {weekday}", self.tz)
                now = datetime(2026, 1, 5, 0, 0, tzinfo=ZoneInfo(self.tz))
                self.assertEqual(trigger.get_next_fire_time(None, now).weekday(), 6)

    def test_weekday_names_and_lists_ranges_steps(self):
        trigger = scheduler.parse_cron_schedule("*/15 8-10/2 * jan,mar mon-fri", self.tz)
        now = datetime(2026, 1, 5, 7, 59, tzinfo=ZoneInfo(self.tz))
        first = trigger.get_next_fire_time(None, now)
        self.assertEqual(first, datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo(self.tz)))
        self.assertEqual(trigger.get_next_fire_time(first, first), datetime(2026, 1, 5, 8, 15, tzinfo=ZoneInfo(self.tz)))

    def test_day_and_weekday_are_or_when_both_restricted(self):
        trigger = scheduler.parse_cron_schedule("0 0 1 * mon", self.tz)
        now = datetime(2026, 1, 1, 0, 1, tzinfo=ZoneInfo(self.tz))
        first = trigger.get_next_fire_time(None, now)
        self.assertEqual(first, datetime(2026, 1, 5, 0, 0, tzinfo=ZoneInfo(self.tz)))
        next_fire = trigger.get_next_fire_time(first, first)
        self.assertEqual(next_fire, datetime(2026, 1, 12, 0, 0, tzinfo=ZoneInfo(self.tz)))

    def test_star_step_is_from_field_minimum(self):
        trigger = scheduler.parse_cron_schedule("0 */6 * * *", self.tz)
        now = datetime(2026, 1, 1, 0, 1, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(trigger.get_next_fire_time(None, now), datetime(2026, 1, 1, 6, 0, tzinfo=ZoneInfo(self.tz)))

    def test_ranges_include_sunday_seven(self):
        values, _ = scheduler._parse_field('1-7', 0, 7, sunday=True)
        self.assertEqual(values, list(range(7)))
        values, _ = scheduler._parse_field('5-7/2', 0, 7, sunday=True)
        self.assertEqual(values, [4, 6])

    def test_star_steps_preserved_in_day_and_weekday(self):
        trigger = scheduler.parse_cron_schedule('0 0 */2 * *', self.tz)
        now = datetime(2026, 1, 2, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(trigger.get_next_fire_time(None, now).day, 3)
        trigger = scheduler.parse_cron_schedule('0 0 * * */2', self.tz)
        now = datetime(2026, 1, 5, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(trigger.get_next_fire_time(None, now).weekday(), 1)

    def test_single_value_step(self):
        trigger = scheduler.parse_cron_schedule('5/15 * * * *', self.tz)
        now = datetime(2026, 1, 1, 0, 6, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(trigger.get_next_fire_time(None, now).minute, 20)

    @patch.object(scheduler.BlockingScheduler, 'start')
    def test_main_can_start_or_trigger(self, start):
        with patch.dict('os.environ', {'CRON_SCHEDULE': '0 1 1 * mon', 'TZ': 'UTC', 'JOB_TIMEOUT_SECONDS': ''}, clear=True):
            scheduler.main()
        start.assert_called_once()

    def test_rejects_invalid_and_unsupported_syntax(self):
        for expression in ("* * * *", "60 * * * *", "*/0 * * * *", "mon-fri/2 * * * *", "1,,2 * * * *", "5-1 * * * *", "? * * * *"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                scheduler.parse_cron_schedule(expression, self.tz)


class RunJobTests(unittest.TestCase):
    @patch.object(scheduler.subprocess, "run")
    def test_uses_absolute_script_cwd_and_default_timeout(self, run):
        with patch.dict("os.environ", {"READ_NUM": "40"}, clear=True):
            scheduler.run_job()
        kwargs = run.call_args.kwargs
        self.assertEqual(run.call_args.args[0][0], scheduler.sys.executable)
        self.assertTrue(run.call_args.args[0][1].endswith("/main.py"))
        self.assertTrue(__import__("os").path.isabs(run.call_args.args[0][1]))
        self.assertEqual(kwargs["cwd"], scheduler.Path(scheduler.__file__).resolve().parent.as_posix())
        self.assertEqual(kwargs["timeout"], max(3600, 40 * 90 + 900))

    @patch.object(scheduler.subprocess, "run")
    def test_timeout_override_and_expiration_logged_without_exception_detail(self, run):
        from subprocess import TimeoutExpired
        run.side_effect = TimeoutExpired("main.py", 123, output="private")
        with patch.dict("os.environ", {"READ_NUM": "1", "JOB_TIMEOUT_SECONDS": "123"}, clear=True):
            with self.assertLogs(level="ERROR") as logs:
                scheduler.run_job()
        self.assertEqual(run.call_args.kwargs["timeout"], 123)
        self.assertNotIn("main.py", " ".join(logs.output))
        self.assertNotIn("private", " ".join(logs.output))

    @patch.object(scheduler.subprocess, "run")
    def test_invalid_read_num_fails_before_subprocess(self, run):
        with patch.dict("os.environ", {"READ_NUM": "bad"}, clear=True):
            with self.assertLogs(level="ERROR"):
                scheduler.run_job()
        run.assert_not_called()

    @patch.object(scheduler.subprocess, "run")
    def test_invalid_timeout_fails_before_subprocess(self, run):
        with patch.dict("os.environ", {"READ_NUM": "1", "JOB_TIMEOUT_SECONDS": "0"}, clear=True):
            with self.assertLogs(level="ERROR"):
                scheduler.run_job()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
