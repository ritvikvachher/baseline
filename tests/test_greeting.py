import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from coreline import greeting, settings
from coreline.greeting import AFTERNOON, EVENING, MORNING, NIGHT, info_line, pick_greeting
from coreline.project import Project

# A Wednesday, so the Friday rule stays out of the way unless a test asks for it.
WED_MORNING = datetime(2026, 10, 7, 9, 30)
YESTERDAY = WED_MORNING - timedelta(days=1)
FAR_DELIVERY = date(2027, 5, 27)


def text(parts):
    return "  ·  ".join("".join(run for run, _style in part) for part in parts)


class GreetingTests(unittest.TestCase):
    def greet(self, now=WED_MORNING, last_seen=YESTERDAY, delivery=FAR_DELIVERY, name="Ritvik"):
        g = pick_greeting(now, name, last_seen, delivery)
        return f"{g.badge} {g.text}, {g.name}".strip() if g.name else f"{g.badge} {g.text}".strip()

    def test_time_slots(self):
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 5, 0)), MORNING)
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 11, 59)), MORNING)
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 12, 0)), AFTERNOON)
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 17, 0)), EVENING)
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 22, 0)), NIGHT)
        self.assertEqual(greeting.time_slot(datetime(2026, 10, 7, 4, 59)), NIGHT)

    def test_time_of_day(self):
        self.assertEqual(self.greet(), "GOOD MORNING, RITVIK")
        self.assertEqual(self.greet(WED_MORNING.replace(hour=14)), "GOOD AFTERNOON, RITVIK")
        self.assertEqual(self.greet(WED_MORNING.replace(hour=19)), "GOOD EVENING, RITVIK")
        self.assertEqual(self.greet(WED_MORNING.replace(hour=23)), "BURNING THE MIDNIGHT OIL, RITVIK")

    def test_visits(self):
        self.assertEqual(self.greet(last_seen=None), "WELCOME TO CORELINE, RITVIK")
        self.assertEqual(self.greet(last_seen=WED_MORNING - timedelta(hours=2)), "BACK AT IT, RITVIK")
        self.assertEqual(self.greet(last_seen=WED_MORNING - timedelta(days=3)), "WELCOME BACK, RITVIK")

    def test_friday_but_not_at_night(self):
        friday = datetime(2026, 10, 9, 15, 0)
        thursday = friday - timedelta(days=1)
        self.assertEqual(self.greet(friday, thursday), "LAST PUSH OF THE WEEK, RITVIK")
        self.assertEqual(self.greet(friday.replace(hour=23), thursday), "BURNING THE MIDNIGHT OIL, RITVIK")

    def test_delivery_countdown(self):
        day = WED_MORNING.date()
        self.assertEqual(self.greet(delivery=day + timedelta(days=12)), "12 DAYS TO DELIVERY. LET'S GO, RITVIK")
        self.assertEqual(self.greet(delivery=day + timedelta(days=1)), "1 DAY TO DELIVERY. LET'S GO, RITVIK")
        self.assertEqual(self.greet(delivery=day), "DELIVERY DAY. BRING IT HOME, RITVIK")
        self.assertEqual(self.greet(delivery=day + timedelta(days=15)), "GOOD MORNING, RITVIK")
        self.assertEqual(self.greet(delivery=day - timedelta(days=1)), "GOOD MORNING, RITVIK")
        # First launch still wins over the countdown.
        self.assertEqual(self.greet(last_seen=None, delivery=day), "WELCOME TO CORELINE, RITVIK")

    def test_unknown_name(self):
        self.assertEqual(self.greet(name=None), "GOOD MORNING")


class InfoLineTests(unittest.TestCase):
    def test_everything(self):
        new = [("Andrew", "SH010 anim v007"), ("Andrew", "SH020 anim v002")]
        self.assertEqual(text(info_line(6, 12, 3, new)),
                         "Week 6 of 12  ·  3 tasks assigned to you  ·  "
                         "Andrew published 2 versions since you were last here")

    def test_one_task_one_publish(self):
        self.assertEqual(text(info_line(6, 12, 1, [("Andrew", "SH010 anim v007")])),
                         "Week 6 of 12  ·  1 task assigned to you  ·  Andrew published SH010 anim v007")

    def test_people(self):
        two = [("Andrew", "a"), ("Mei", "b"), ("Andrew", "c")]
        self.assertEqual(text(info_line(6, 12, 0, two)),
                         "Week 6 of 12  ·  Andrew and Mei published 3 versions since you were last here")
        three = [("Andrew", "a"), ("Mei", "b"), ("Lele", "c")]
        self.assertIn("Andrew and 2 others published 3 versions", text(info_line(6, 12, 0, three)))

    def test_caught_up_and_no_schedule(self):
        self.assertEqual(text(info_line(6, 12, 0, [])), "Week 6 of 12  ·  All caught up")
        self.assertEqual(text(info_line(6, None, 3, [], first_launch=True)), "3 tasks assigned to you")
        self.assertEqual(info_line(6, None, 0, [], first_launch=True), [])


class NameAndVisitTests(unittest.TestCase):
    TEAM = {"members": {"s5707305": {"name": "Ritvik Vachher", "short": "ritvik"}}}

    def test_first_names(self):
        with mock.patch.object(settings, "load_team", return_value=self.TEAM):
            self.assertEqual(settings.first_name("ritvik"), "Ritvik")
            self.assertEqual(settings.first_name("guest"), "Guest")
            self.assertEqual(settings.first_name("ritvikvachher"), "Ritvik")
            with mock.patch("getpass.getuser", return_value="s5707305"):
                self.assertEqual(settings.first_name(), "Ritvik")
            with mock.patch("getpass.getuser", return_value="s0000000"), \
                    mock.patch.dict("os.environ", {"CORELINE_USER": ""}), \
                    mock.patch.object(settings, "get_user_setting", return_value=None):
                self.assertIsNone(settings.first_name())

    def test_visits_are_remembered_per_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(Path(tmp), {"name": "T", "layout": {"tracking": "_ADMIN/coreline"}})
            self.assertIsNone(project.start_visit("ritvik"))
            self.assertIsInstance(project.start_visit("ritvik"), datetime)
            self.assertIsNone(project.start_visit("andrew"))
            self.assertTrue((Path(tmp) / "_ADMIN/coreline/users/ritvik.json").is_file())


if __name__ == "__main__":
    unittest.main()
