"""World Cup 2022 (Qatar) dataset + helpers for prototyping a draft scoring system.

Data files (same directory):
  - matches.csv   : all 64 results (48 group + 16 knockout). Knockout shootouts
                    are recorded in the pens1/pens2 columns; score1/score2 are the
                    scoreline at the end of normal/extra time.
  - rankings.csv  : FIFA world ranking of all 32 teams as of 6 Oct 2022 (the last
                    ranking published before the tournament).

Stage codes in matches.csv: group, R16, QF, SF, 3P (third-place), F (final).

Typical use:
    from wc2022_data import load_matches, load_rankings, team_summaries
    matches = load_matches()
    ranks   = load_rankings()
    summ    = team_summaries()          # per-team aggregate stats
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Optional

_DIR = os.path.dirname(os.path.abspath(__file__))

# Round-reached labels, ordered weakest -> strongest, useful for progression scoring.
STAGE_ORDER = ["group", "R16", "QF", "SF", "3P", "F"]


@dataclass
class Match:
    stage: str
    group: str            # "A".."H" for group games, "" for knockout
    team1: str
    score1: int
    score2: int
    team2: str
    pens1: Optional[int] = None
    pens2: Optional[int] = None

    @property
    def is_knockout(self) -> bool:
        return self.stage != "group"

    @property
    def went_to_pens(self) -> bool:
        return self.pens1 is not None

    @property
    def winner(self) -> Optional[str]:
        """Match winner. Draws in the group stage return None."""
        if self.went_to_pens:
            return self.team1 if self.pens1 > self.pens2 else self.team2
        if self.score1 > self.score2:
            return self.team1
        if self.score2 > self.score1:
            return self.team2
        return None  # genuine draw (only possible in the group stage)

    def goals_for(self, team: str) -> int:
        return self.score1 if team == self.team1 else self.score2

    def goals_against(self, team: str) -> int:
        return self.score2 if team == self.team1 else self.score1


def load_matches(path: str = None) -> list[Match]:
    path = path or os.path.join(_DIR, "matches.csv")
    out: list[Match] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.append(
                Match(
                    stage=r["stage"],
                    group=r["group"],
                    team1=r["team1"],
                    score1=int(r["score1"]),
                    score2=int(r["score2"]),
                    team2=r["team2"],
                    pens1=int(r["pens1"]) if r["pens1"] else None,
                    pens2=int(r["pens2"]) if r["pens2"] else None,
                )
            )
    return out


def load_rankings(path: str = None) -> dict[str, dict]:
    """Return {team: {"group": str, "fifa_rank": int}}."""
    path = path or os.path.join(_DIR, "rankings.csv")
    out: dict[str, dict] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[r["team"]] = {"group": r["group"], "fifa_rank": int(r["fifa_rank"])}
    return out


@dataclass
class TeamSummary:
    team: str
    group: str = ""
    fifa_rank: int = 0
    played: int = 0
    wins: int = 0          # includes shootout wins
    draws: int = 0         # group-stage draws only
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    clean_sheets: int = 0
    shootout_wins: int = 0
    stage_reached: str = "group"   # furthest stage the team appeared in

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against


def team_summaries() -> dict[str, TeamSummary]:
    """Aggregate every team's tournament into a TeamSummary."""
    ranks = load_rankings()
    summ: dict[str, TeamSummary] = {
        t: TeamSummary(team=t, group=info["group"], fifa_rank=info["fifa_rank"])
        for t, info in ranks.items()
    }
    stage_idx = {s: i for i, s in enumerate(STAGE_ORDER)}

    for m in load_matches():
        for team, opp_goals_attr in ((m.team1, "score2"), (m.team2, "score1")):
            s = summ[team]
            s.played += 1
            gf, ga = m.goals_for(team), m.goals_against(team)
            s.goals_for += gf
            s.goals_against += ga
            if ga == 0:
                s.clean_sheets += 1
            w = m.winner
            if w is None:
                s.draws += 1
            elif w == team:
                s.wins += 1
                if m.went_to_pens:
                    s.shootout_wins += 1
            else:
                s.losses += 1
            # furthest stage reached
            if stage_idx[m.stage] > stage_idx[s.stage_reached]:
                s.stage_reached = m.stage
    return summ


# --------------------------------------------------------------------------- #
# Example scoring framework — a starting point to tweak for your draft game.
# --------------------------------------------------------------------------- #

# How far each team got, expressed as a "finish" bonus you can tune.
# (champion, runner-up, 3rd, 4th, QF, R16, group) handled in score_team below.
DEFAULT_POINTS = {
    "win": 3,            # per match win (incl. shootout)
    "draw": 1,           # per group-stage draw
    "goal_for": 1,       # per goal scored
    "goal_against": -1,  # per goal conceded
    "clean_sheet": 2,    # per clean sheet
    "reach_R16": 4,      # one-off bonus for getting out of the group
    "reach_QF": 6,
    "reach_SF": 8,
    "reach_final": 12,
    "win_final": 20,     # champion bonus (on top of reaching the final)
    # Upset bonus: extra points for beating a team ranked this many or more
    # places ABOVE you. Set to 0 to disable.
    "upset_rank_gap": 10,
    "upset_bonus": 3,
}


def score_team(team: str, points: dict = None) -> dict:
    """Score a single team's tournament under a configurable points scheme.

    Returns a breakdown dict including the total, so you can sanity-check and
    tune the weights. This is deliberately simple — swap in your own rules.
    """
    p = {**DEFAULT_POINTS, **(points or {})}
    ranks = load_rankings()
    matches = load_matches()
    s = team_summaries()[team]

    breakdown = {
        "wins": s.wins * p["win"],
        "draws": s.draws * p["draw"],
        "goals_for": s.goals_for * p["goal_for"],
        "goals_against": s.goals_against * p["goal_against"],
        "clean_sheets": s.clean_sheets * p["clean_sheet"],
    }

    # Progression bonuses (cumulative as the team advances).
    reached = s.stage_reached
    idx = STAGE_ORDER.index(reached)
    prog = 0
    if idx >= STAGE_ORDER.index("R16"):
        prog += p["reach_R16"]
    if idx >= STAGE_ORDER.index("QF"):
        prog += p["reach_QF"]
    # SF appearance: reached SF, F, or played the 3rd-place match.
    if reached in ("SF", "3P", "F"):
        prog += p["reach_SF"]
    if reached == "F":
        prog += p["reach_final"]
    # Champion: won the final.
    final = next(m for m in matches if m.stage == "F")
    if final.winner == team:
        prog += p["win_final"]
    breakdown["progression"] = prog

    # Upset bonus: beating much higher-ranked opponents.
    upset = 0
    if p["upset_bonus"]:
        my_rank = ranks[team]["fifa_rank"]
        for m in matches:
            if m.winner != team:
                continue
            opp = m.team2 if team == m.team1 else m.team1
            if my_rank - ranks[opp]["fifa_rank"] >= p["upset_rank_gap"]:
                upset += p["upset_bonus"]
    breakdown["upset_bonus"] = upset

    breakdown["TOTAL"] = sum(breakdown.values())
    return breakdown


if __name__ == "__main__":
    # Quick self-check + a demo league table under the default scheme.
    s = team_summaries()
    assert len(load_matches()) == 64, "expected 64 matches"
    assert len(s) == 32, "expected 32 teams"
    # Argentina won it all: 7 played, sanity check goals.
    arg = s["Argentina"]
    assert arg.stage_reached == "F" and arg.played == 7, arg

    print(f"{'TEAM':<16}{'RANK':>5}{'STAGE':>7}{'SCORE':>7}")
    table = sorted(
        ((t, score_team(t)["TOTAL"]) for t in s),
        key=lambda x: x[1],
        reverse=True,
    )
    for team, total in table:
        print(f"{team:<16}{s[team].fifa_rank:>5}{s[team].stage_reached:>7}{total:>7}")
