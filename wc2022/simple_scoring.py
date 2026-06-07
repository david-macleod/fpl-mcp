"""Simple draft scoring: 3 pts win, 1 pt draw, 1 pt per goal — judged at 90 mins.

Everything is scored as it stood at the end of regulation (including stoppage
time). Extra-time goals and penalty shootouts are ignored, so the 5 matches that
went to penalties all count as draws. There is no knockout/progression bonus.

Two knockout scorelines differ from the recorded full-time (after-ET) score and
are overridden below to their 90-minute value.
"""

from __future__ import annotations

from wc2022_data import load_matches, load_rankings
from draft import snake_draft

# Matches where the 90' score differs from the recorded after-extra-time score.
REG90 = {
    ("QF", "Croatia", "Brazil"): (0, 0),     # Neymar & Petkovic both scored in ET
    ("F", "Argentina", "France"): (2, 2),    # 3-3 came in ET; it was 2-2 at 90'
}

GROUP_STAGE = {"group"}
ALL_STAGES = {"group", "R16", "QF", "SF", "3P", "F"}


def reg90(m):
    """Return the (team1, team2) goals as they stood at 90 minutes."""
    return REG90.get((m.stage, m.team1, m.team2), (m.score1, m.score2))


def simple_score(team: str, stages: set) -> dict:
    w = d = goals = 0
    for m in load_matches():
        if m.stage not in stages or team not in (m.team1, m.team2):
            continue
        g1, g2 = reg90(m)
        gf, ga = (g1, g2) if team == m.team1 else (g2, g1)
        goals += gf
        if gf > ga:
            w += 1
        elif gf == ga:
            d += 1
    result_pts = 3 * w + d           # points from wins/draws
    return {"W": w, "D": d, "goals": goals,
            "result_pts": result_pts, "goal_pts": goals,
            "pts": result_pts + goals}


def table(rosters: dict[int, list[str]], stages: set, title: str) -> None:
    ranks = load_rankings()
    print("=" * 60)
    print(title)
    print("=" * 60)
    player_total: dict[int, int] = {}
    for player, teams in rosters.items():
        rows = [(t, simple_score(t, stages)) for t in teams]
        total = sum(s["pts"] for _, s in rows)
        res = sum(s["result_pts"] for _, s in rows)
        gls = sum(s["goal_pts"] for _, s in rows)
        player_total[player] = total
        print(f"\nPlayer {player}  —  {total} pts   [result {res}, goals {gls}]")
        print(f"   {'team':<16}{'split':>10}{'pts':>5}")
        for t, s in sorted(rows, key=lambda r: r[1]["pts"], reverse=True):
            split = f"[{s['result_pts']}, {s['goal_pts']}]"
            print(f"   {t:<16}{split:>10}{s['pts']:>5}")

    print("\n   --- standings ---")
    for pos, (p, tot) in enumerate(
        sorted(player_total.items(), key=lambda x: x[1], reverse=True), 1
    ):
        print(f"   {pos}. Player {p}   {tot} pts")
    print()


if __name__ == "__main__":
    rosters = snake_draft(n_players=5, rounds=6)
    table(rosters, GROUP_STAGE, "AFTER GROUP STAGE")
    table(rosters, ALL_STAGES, "AFTER FINAL (whole tournament)")
