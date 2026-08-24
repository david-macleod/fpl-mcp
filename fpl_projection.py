"""Per-gameweek points projections that survive the season rollover.

The trap this module exists to avoid: ``bootstrap-static`` resets
``total_points``, ``starts`` and ``minutes`` when a new season begins, so any
model reading those fields silently degrades to noise in Gameweek 1 and stays
there until enough matches accumulate. Last season's record only survives in
each player's ``element-summary`` under ``history_past``.

A projection blends three things:

* last season's points per 90, weighted by how much they actually played
* this season's points per 90, weighted up as minutes accumulate
* the fixture: difficulty, home advantage, and availability

Players with no Premier League record - promoted clubs, overseas signings -
fall back to a price-implied prior, which is weak but honest.

Usage::

    from fpl_projection import Projector
    proj = Projector(bootstrap, fixtures, history_past)
    p = proj.project(element, gameweek)
"""

# Fixture difficulty multipliers. Defenders and keepers live on clean sheets,
# so difficulty swings them harder than it does attackers.
FIXTURE_MULT = {
    1: {1: 1.35, 2: 1.25, 3: 1.00, 4: 0.82, 5: 0.68},   # GKP
    2: {1: 1.35, 2: 1.25, 3: 1.00, 4: 0.82, 5: 0.68},   # DEF
    3: {1: 1.22, 2: 1.15, 3: 1.00, 4: 0.90, 5: 0.80},   # MID
    4: {1: 1.22, 2: 1.15, 3: 1.00, 4: 0.90, 5: 0.80},   # FWD
}
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

FULL_SEASON_MINUTES = 2400      # ~27 full matches: treated as fully reliable
MIN_MINUTES_FOR_RATE = 450      # below this, last season's rate is too noisy
THIS_SEASON_FULL_WEIGHT = 900   # minutes at which this season dominates
SHRINK_MINUTES = 540            # ~6 matches: how fast a newcomer earns trust


class Projector:
    def __init__(self, bootstrap, fixtures, history_past):
        """
        :param dict bootstrap: bootstrap-static payload.
        :param list fixtures: fixtures payload.
        :param dict history_past: ``{element_id: {last_points, last_minutes}}``
            as cached from element-summary. Keys may be str or int.
        """
        self.teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
        self.fixtures = fixtures
        self.past = {int(k): v for k, v in history_past.items()}

    def fixture(self, team_id, gameweek):
        """Returns (opponent label, difficulty, is_home) or (None, None, None)."""
        for f in self.fixtures:
            if f["event"] != gameweek:
                continue
            if f["team_h"] == team_id:
                return f"{self.teams[f['team_a']]}(H)", f["team_h_difficulty"], True
            if f["team_a"] == team_id:
                return f"{self.teams[f['team_h']]}(A)", f["team_a_difficulty"], False
        return None, None, None

    def _rate(self, element):
        """Points per 90 and how much to trust it.

        Returns (rate, reliability, source).
        """
        past = self.past.get(element["id"], {})
        last_pts, last_mins = past.get("last_points", 0), past.get("last_minutes", 0)
        this_pts, this_mins = element["total_points"], element["minutes"]

        last_rate = last_pts / (last_mins / 90.0) if last_mins >= MIN_MINUTES_FOR_RATE else None
        this_rate = this_pts / (this_mins / 90.0) if this_mins >= 60 else None

        if last_rate is None and this_rate is None:
            # No usable record: infer from what FPL charges for him.
            prior = max(1.8, (element["now_cost"] / 10.0 - 3.8) * 0.62)
            return prior, 0.55, "price-prior"

        if last_rate is None:
            # No Premier League history. A single good match reads as a
            # spectacular per-90 rate - 14 points in 75 minutes is 16.8 -
            # so shrink hard toward the price prior until the minutes are
            # there to support it.
            prior = max(1.8, (element["now_cost"] / 10.0 - 3.8) * 0.62)
            w = this_mins / (this_mins + SHRINK_MINUTES)
            rate = prior * (1 - w) + this_rate * w
            reliability = min(0.85, 0.45 + this_mins / 1800.0)
            return rate, reliability, "newcomer"

        if this_rate is None:
            reliability = min(1.0, last_mins / FULL_SEASON_MINUTES)
            return last_rate, reliability, "last-season"

        # Both available: let this season take over as minutes accumulate.
        w = min(0.6, this_mins / THIS_SEASON_FULL_WEIGHT)
        rate = last_rate * (1 - w) + this_rate * w
        reliability = min(1.0, max(last_mins / FULL_SEASON_MINUTES,
                                   this_mins / 270.0))
        return rate, reliability, "blended"

    def availability(self, element):
        """Multiplier for injury/suspension risk, plus a readable flag."""
        chance = element.get("chance_of_playing_next_round")
        if element["status"] != "a":
            if chance is None:
                return 0.0, f"OUT({element['status']})"
            return chance / 100.0, f"{chance}%"
        if chance is not None and chance < 100:
            return chance / 100.0, f"{chance}%"
        return 1.0, ""

    def project(self, element, gameweek):
        """Projected points for one player in one gameweek, or None if no fixture."""
        opp, diff, home = self.fixture(element["team"], gameweek)
        if opp is None:
            return None

        rate, reliability, source = self._rate(element)
        avail, flag = self.availability(element)
        mult = FIXTURE_MULT[element["element_type"]][diff]
        home_bonus = 1.06 if home else 0.96

        # A points-per-90 rate has to be discounted by the chance he plays 90.
        expected = rate * reliability * mult * home_bonus * avail

        return {
            "id": element["id"],
            "name": element["web_name"],
            "team": self.teams[element["team"]],
            "pos": POS[element["element_type"]],
            "etype": element["element_type"],
            "cost": element["now_cost"],
            "proj": expected,
            "rate": rate,
            "reliability": reliability,
            "source": source,
            "avail": avail,
            "flag": flag,
            "opp": opp,
            "diff": diff,
            "own": float(element["selected_by_percent"]),
        }

    def horizon(self, element, first, last):
        """Average projection across a run of gameweeks."""
        vals = [self.project(element, gw) for gw in range(first, last + 1)]
        vals = [v["proj"] for v in vals if v]
        return sum(vals) / len(vals) if vals else 0.0
