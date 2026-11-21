"""Aivana failures are explained in plain words, fallbacks are labelled, simulated data is flagged."""
import pytest

from aivana_client import AivanaClient, friendly_error
from cost_calc import CostCalc
from tests.test_cost_calc import DATA, req
from tests.test_options import FakeSDK, res, rows


class AuthError(Exception): pass
class RateLimitError(Exception): pass
class UpstreamError(Exception): pass
class InsufficientBalance(Exception): pass


@pytest.mark.parametrize("exc, words", [
    (AuthError("401 invalid api key"), "rejected the API key"),
    (RateLimitError("429 too many requests"), "rate limit"),
    (RateLimitError("429 spend limit reached"), "out of credit"),
    (InsufficientBalance("402 balance too low for web search"), "out of credit"),
    (TimeoutError("read timed out"), "too long"),
    (ConnectionError("Failed to resolve api.aivana.ai"), "couldn't reach"),
    (UpstreamError("structured_output_failed"), "didn't match"),
    (ValueError("Aivana returned package 99, which isn't one of the 12 priced packages"), "didn't match"),
    (RuntimeError("the 'aivana' package isn't installed (pip install -r requirements.txt)"), "isn't set up"),
])
def test_friendly_errors(exc, words):
    msg = friendly_error(exc)
    assert words in msg and "ai_live" not in msg


def client():
    c = AivanaClient(sdk=FakeSDK)
    c.api_key = "ai_live_secret"
    return c


def test_failed_recommendation_note_is_plain_and_labelled():
    def boom(kw):
        raise RateLimitError("429 too many requests")
    FakeSDK.reply = boom
    rec = client().recommend({"diet_tags": ["gluten_free"]}, rows())
    assert rec["source"] == "ranking"
    assert "rate limit" in rec["note"] and "app's own best-value ranking" in rec["note"]
    assert "RateLimitError" not in rec["note"] and "ai_live" not in rec["note"]


def test_local_reason_uses_readable_diet_names():
    rec = AivanaClient().recommend({"diet_tags": ["gluten_free", "halal"]}, rows())
    assert "gluten_free" not in rec["reason"]


def booked_package():
    p = CostCalc(DATA).generate_packages(req())[0]
    return {"package_no": p.package_no, "total_cost": p.total_cost, "detail": p.detail}


TRIP = {"venue_name": "Ohio Stadium", "opponent": "Ohio State", "match_at": "2026-11-28T15:30", "nights": 1}


def test_draft_without_aivana_is_labelled_template():
    d = AivanaClient().draft_announcement(TRIP, booked_package())
    assert d["source"] == "template" and "template" in d["note"]


def test_aivana_draft_is_plain_text():
    FakeSDK.reply = res(structured={"title": "**Road trip!**", "body": "## Plan\n**Bus:** leaves at 9.\nSee you there."})
    d = client().draft_announcement(TRIP, booked_package())
    assert d["source"] == "aivana" and "*" not in d["body"] + d["title"] and "#" not in d["body"]
    assert d["title"] == "Road trip!" and "Bus: leaves at 9." in d["body"]
    assert "Don't mention money".lower() in FakeSDK.calls[-1]["system"].lower()


def test_failed_draft_falls_back_with_a_note():
    def boom(kw):
        raise TimeoutError("timed out")
    FakeSDK.reply = boom
    d = client().draft_announcement(TRIP, booked_package())
    assert d["source"] == "template" and "too long" in d["note"]


def test_restaurants_that_cannot_seat_the_group_are_flagged():
    pkgs = CostCalc(DATA).generate_packages(req(num_players=60))      # 65 travelers; catalog seats are smaller
    rests = [p.detail["restaurant"] for p in pkgs if p.detail["restaurant"]]
    assert rests
    for r in rests:
        assert r["fits_group"] is (int(r["group_capacity"]) >= 65)


def test_restaurants_that_seat_the_group_are_not_flagged():
    pkgs = CostCalc(DATA).generate_packages(req(num_players=10))
    assert all(p.detail["restaurant"]["fits_group"] for p in pkgs)
