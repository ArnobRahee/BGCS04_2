"""Three distinct options (lowest cost, closest, best value) and Aivana's validated pick."""
import pytest

from aivana_client import AivanaClient
from cost_calc import CostCalc
from tests.test_cost_calc import DATA, req


def marked(pkgs):
    return {p.detail["highlight"]: p for p in pkgs if p.detail.get("highlight")}


@pytest.mark.parametrize("kw", [{}, {"nights": 2}, {"budget": 2000}, {"num_players": 10},
                                {"venue_lat": 41.65, "venue_lng": -83.61}])   # Ohio Stadium, tight budget, Toledo
def test_three_different_packages_are_highlighted(kw):
    pkgs = CostCalc(DATA).generate_packages(req(**kw))
    m = marked(pkgs)
    assert set(m) == {"lowest_cost", "closest", "best_value"}
    assert len({p.package_no for p in m.values()}) == 3
    hotels = {p.detail["hotel"]["id"] for p in m.values()}
    assert len(hotels) == 3                                  # the catalog has enough hotels near each venue


def test_highlights_are_what_they_say():
    pkgs = CostCalc(DATA).generate_packages(req())
    m = marked(pkgs)
    assert m["lowest_cost"].total_cost == min(p.total_cost for p in pkgs)
    km = lambda p: p.detail["hotel"]["km_to_venue"]
    others = [p for p in pkgs if p.detail["hotel"]["id"] != m["lowest_cost"].detail["hotel"]["id"]]
    assert km(m["closest"]) == min(km(p) for p in others)
    if not m["lowest_cost"].detail.get("also_closest"):
        assert km(m["closest"]) == min(km(p) for p in pkgs)
    rest = [p for p in pkgs if p.detail["hotel"]["id"] not in {km_p.detail["hotel"]["id"] for km_p in (m["lowest_cost"], m["closest"])}]
    assert m["best_value"].detail["value_score"] == min(p.detail["value_score"] for p in rest)


def test_highlighted_packages_survive_the_twelve_package_cap():
    pkgs = CostCalc(DATA).generate_packages(req())
    assert len(pkgs) <= 12 and len(marked(pkgs)) == 3
    assert [p.package_no for p in pkgs] == list(range(1, len(pkgs) + 1))


def test_day_trip_without_meals_has_a_single_package():
    pkgs = CostCalc(DATA).generate_packages(req(nights=0, meals=0))
    assert len(pkgs) == 1 and pkgs[0].detail["highlight"] == "lowest_cost"


def test_value_score_prefers_within_budget():
    pkgs = CostCalc(DATA).generate_packages(req(budget=3300))
    inside = [p for p in pkgs if p.detail["within_budget"]]
    outside = [p for p in pkgs if not p.detail["within_budget"]]
    if inside and outside:
        assert max(p.detail["value_score"] for p in inside) < min(p.detail["value_score"] for p in outside) + 1


# -- Aivana's pick ---------------------------------------------------------
class FakeSDK:
    api_key = None
    calls = []
    reply = None

    @classmethod
    def generate(cls, **kw):
        cls.calls.append(kw)
        r = cls.reply(kw) if callable(cls.reply) else cls.reply
        if isinstance(r, Exception):
            raise r
        return r


def res(structured=None, answer=None):
    return type("Res", (), {"structured": structured, "answer": answer})()


def rows():
    return [{"package_no": p.package_no, "total_cost": p.total_cost, "detail": p.detail}
            for p in CostCalc(DATA).generate_packages(req())]


def client():
    c = AivanaClient(sdk=FakeSDK)
    c.api_key = "ai_live_test"
    return c


def test_recommendation_uses_a_json_schema_limited_to_real_packages():
    pkgs = rows()
    FakeSDK.reply = res(structured={"package_no": 3, "reason": "Close and within budget."})
    rec = client().recommend({"budget": 9000}, pkgs)
    assert rec == {"package_no": 3, "reason": "Close and within budget.", "source": "aivana"}
    fmt = FakeSDK.calls[-1]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["package_no"]["enum"] == [p["package_no"] for p in pkgs]
    assert "never recompute" in FakeSDK.calls[-1]["system"]


def test_package_number_sent_as_text_is_accepted():
    FakeSDK.reply = res(structured={"package_no": "2", "reason": "ok"})
    assert client().recommend({}, rows())["package_no"] == 2


def test_json_in_the_answer_is_accepted_when_structured_is_missing():
    FakeSDK.reply = res(answer='```json\n{"package_no": 4, "reason": "ok"}\n```')
    assert client().recommend({}, rows())["package_no"] == 4


@pytest.mark.parametrize("bad", [{"package_no": 99, "reason": "x"}, {"package_no": "two", "reason": "x"}, {"reason": "x"}])
def test_invented_package_falls_back_to_the_marked_best_value(bad):
    pkgs = rows()
    FakeSDK.reply = res(structured=bad)
    rec = client().recommend({}, pkgs)
    best = next(p for p in pkgs if p["detail"]["highlight"] == "best_value")
    assert rec["source"] == "ranking" and rec["package_no"] == best["package_no"] and "note" in rec


def test_schema_rejection_retries_without_enum():
    def reply(kw):
        if "enum" in kw["response_format"]["schema"]["properties"]["package_no"]:
            return RuntimeError("InvalidRequestError: unsupported keyword enum")
        return res(structured={"package_no": 5, "reason": "ok"})

    class Err(Exception):
        pass

    def raising(kw):
        r = reply(kw)
        if isinstance(r, Exception):
            raise Err("InvalidRequestError: unsupported keyword enum")
        return r
    FakeSDK.reply = raising
    rec = client().recommend({}, rows())
    assert rec["package_no"] == 5 and rec["source"] == "aivana"


def test_aivana_never_approves_a_package():
    """A recommendation only marks a package; the trip stays in PLANNING with nothing selected."""
    import app as appmod
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db, aivana=client())
    FakeSDK.reply = res(structured={"package_no": 1, "reason": "ok"})
    appmod.seed_demo(db, planner, with_trip=True)
    trip = db.select("trips")[0]
    assert trip["ai_recommendation"]["source"] == "aivana"
    assert trip["status"] == "PLANNING" and trip["selected_package_no"] is None
    assert db.select("bookings") == []
