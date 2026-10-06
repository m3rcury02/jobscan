"""Run: python -m pytest tests/

Board health (2026-10-06): TCS was down five days and RippleHire two with
only a footer count in the digest. These pin the down / recovered rules and
the once-a-day email slot.
"""
from datetime import datetime, timedelta, timezone

import jobscan as J

T0 = datetime(2026, 10, 6, 0, 17, tzinfo=timezone.utc)
CO = [{"Company": "TCS", "Referral": "yes"}, {"Company": "Acme", "Referral": ""}]


def run(health, outcomes, at):
    return J.update_health(health, [(c, jobs, err) for c, jobs, err in outcomes], at)


def test_a_failing_board_is_down_only_after_the_grace_period():
    h = run({}, [(CO[0], None, "JSONDecodeError: Expecting value")], T0)
    assert J.boards_down(h, CO, T0 + timedelta(hours=1)) == []
    h = run(h, [(CO[0], None, "JSONDecodeError: Expecting value")], T0 + timedelta(hours=7))
    (down,) = J.boards_down(h, CO, T0 + timedelta(hours=7))
    name, since, why, ref = down
    assert name == "TCS" and ref and since.startswith("2026-10-06T00:17")
    assert "JSONDecodeError" in why


def test_a_board_that_empties_out_is_down_but_a_small_one_is_not():
    h = run({}, [(CO[0], [{}] * 400, None), (CO[1], [{}] * 2, None)], T0)
    later = T0 + timedelta(hours=8)
    h = run(h, [(CO[0], [], None), (CO[1], [], None)], T0 + timedelta(hours=1))
    down = J.boards_down(h, CO, later)
    assert [d[0] for d in down] == ["TCS"]
    assert "had 400" in down[0][2]


def test_recovery_clears_the_board_and_removed_rows_are_forgotten():
    h = run({}, [(CO[0], None, "boom"), (CO[1], [{}], None)], T0)
    h = run(h, [(CO[0], [{}] * 3, None)], T0 + timedelta(hours=9))
    assert "down_since" not in h["TCS"] and h["TCS"]["n"] == 3
    assert "Acme" not in h
    assert J.boards_down(h, CO, T0 + timedelta(hours=9)) == []


def test_referral_boards_are_listed_first():
    h = run({}, [(CO[1], None, "x"), (CO[0], None, "y")], T0)
    down = J.boards_down(h, CO, T0 + timedelta(hours=7))
    assert [d[0] for d in down] == ["TCS", "Acme"]


def test_digest_opens_with_boards_down():
    down = [("TCS", "2026-10-01T18:23+00:00", "JSONDecodeError", True)]
    text, _ = J.build_digest([], {}, 0, 1, "2026-10-06", 2, (), down)
    head = text.split("APPLY FIRST")[0]
    assert "BOARDS DOWN (1)" in head and "TCS *REFERRAL*: since 2026-10-01 18:23" in head


def test_daily_slot_is_once_per_ist_day_after_0730(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "DAILY_SENT", tmp_path / "daily_sent.txt")
    monkeypatch.setenv("MODE", "scan")

    class Clock(datetime):
        now_ist = None

        @classmethod
        def now(cls, tz=None):
            return cls.now_ist.astimezone(tz) if tz else cls.now_ist

    monkeypatch.setattr(J, "datetime", Clock)
    Clock.now_ist = datetime(2026, 10, 6, 7, 10, tzinfo=J.IST)
    assert not J._daily_due()
    Clock.now_ist = datetime(2026, 10, 6, 7, 40, tzinfo=J.IST)
    assert J._daily_due()
    J.DAILY_SENT.write_text("2026-10-06\n")
    Clock.now_ist = datetime(2026, 10, 6, 15, 0, tzinfo=J.IST)
    assert not J._daily_due()
    Clock.now_ist = datetime(2026, 10, 7, 9, 0, tzinfo=J.IST)
    assert J._daily_due()
    monkeypatch.setenv("MODE", "backlog")
    Clock.now_ist = datetime(2026, 10, 7, 1, 0, tzinfo=J.IST)
    assert J._daily_due()


def test_a_firecrawl_row_skipped_to_save_credits_is_not_empty(monkeypatch):
    fc = {"Company": "Example", "ATS": "firecrawl"}
    h = run({}, [(fc, [{}] * 20, None)], T0)
    monkeypatch.setattr(J, "_firecrawl_due", lambda: False)
    h = run(h, [(fc, [], None)], T0 + timedelta(hours=1))
    assert J.boards_down(h, [fc], T0 + timedelta(hours=9)) == []
    assert h["Example"]["n"] == 20
