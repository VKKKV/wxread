import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import scheduler


class CronParsingTests(unittest.TestCase):
    tz = "UTC"

    def next_fire(self, expression, now):
        trigger = scheduler.parse_cron_schedule(expression, self.tz)
        self.assertIsInstance(trigger, scheduler.CronTrigger)
        return trigger.get_next_fire_time(None, now)

    def test_default_schedule_retained(self):
        now = datetime(2026, 1, 1, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(self.next_fire("0 1 * * *", now), now.replace(hour=1))

    def test_monday_zero_and_sunday_six(self):
        now = datetime(2026, 1, 5, tzinfo=ZoneInfo(self.tz))
        for weekday, expected in (("0", 0), ("mon", 0), ("6", 6), ("sun", 6), ("SUN", 6)):
            with self.subTest(weekday=weekday):
                self.assertEqual(self.next_fire(f"0 0 * * {weekday}", now).weekday(), expected)

    def test_mon_sun_range_covers_whole_week(self):
        for day in range(5, 12):
            now = datetime(2026, 1, day, tzinfo=ZoneInfo(self.tz))
            self.assertEqual(self.next_fire("0 0 * * mon-sun", now), now)

    def test_weekday_names_and_lists_ranges_steps(self):
        trigger = scheduler.parse_cron_schedule("*/15 8-10/2 * jan,mar mon-fri", self.tz)
        now = datetime(2026, 1, 5, 7, 59, tzinfo=ZoneInfo(self.tz))
        first = trigger.get_next_fire_time(None, now)
        self.assertEqual(first, now.replace(hour=8, minute=0))
        self.assertEqual(trigger.get_next_fire_time(first, first), first.replace(minute=15))

    def test_day_and_weekday_must_both_match(self):
        now = datetime(2026, 1, 1, 0, 1, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(self.next_fire("0 0 1 * mon", now),
                         datetime(2026, 6, 1, tzinfo=ZoneInfo(self.tz)))

    def test_star_and_single_value_steps(self):
        now = datetime(2026, 1, 1, 0, 6, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(self.next_fire("0 */6 * * *", now), now.replace(hour=6, minute=0))
        self.assertEqual(self.next_fire("5/15 * * * *", now), now.replace(minute=20))
        now = datetime(2026, 1, 2, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(self.next_fire("0 0 */2 * *", now).day, 3)
        now = datetime(2026, 1, 5, tzinfo=ZoneInfo(self.tz))
        self.assertEqual(self.next_fire("0 0 * * */2", now).weekday(), 0)

    @patch.object(scheduler, 'BlockingScheduler')
    def test_main_starts_cron_trigger_and_preserves_job_protections(self, cls):
        cls.return_value.timezone = ZoneInfo(self.tz)
        with patch.dict('os.environ', {'CRON_SCHEDULE': '0 1 1 * mon', 'TZ': self.tz,
                                      'JOB_TIMEOUT_SECONDS': ''}, clear=True):
            scheduler.main()
        cls.assert_called_once_with(timezone=self.tz)
        args, kwargs = cls.return_value.add_job.call_args
        self.assertIs(args[0], scheduler.run_job)
        self.assertIsInstance(args[1], scheduler.CronTrigger)
        self.assertEqual(kwargs, dict(max_instances=1, coalesce=True, misfire_grace_time=3600))
        cls.return_value.start.assert_called_once()

    def test_rejects_invalid_fields_using_cron_trigger(self):
        for expression in ("* * * *", "* * * * * *", "60 * * * *", "*/0 * * * *",
                           "mon-fri/2 * * * *", "5-1 * * * *", "? * * * *", "0 0 * * 7"):
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
