"""Official WC2022 draft scoring (the agreed "Sim 2" policy).

BASE (per match, judged at 90 minutes — extra time and penalty shootouts are
ignored, so the five matches that went to penalties count as draws):
    win = 3, draw = 1, +1 per goal scored.

UNDERDOG BONUS (additive, tier-based):
    Teams are split into 3 tiers by draft round: rounds 1-2 -> Tier 1 (strongest),
    3-4 -> Tier 2, 5-6 -> Tier 3. Undrafted teams fall in the bottom tier.
    If you WIN OR DRAW against a team N tiers above you (N = 1 or 2), you get
    +N on the result AND +N per goal scored in that match.
    A DEFEAT scores base only (consolation goals still count 1 each, no bonus).

Two knockout scorelines differ from the recorded after-ET score and are
overridden to their 90-minute value below.
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


def build_tiers(n_players: int = 5, rounds: int = 6) -> dict[str, int]:
    """Map every team to a tier from the best-available draft order.

    rounds 1-2 -> tier 1, 3-4 -> tier 2, 5-6 -> tier 3; undrafted -> bottom tier.
    """
    ranks = load_rankings()
    order = sorted(ranks, key=lambda t: ranks[t]["fifa_rank"])  # draft = rank order
    n_tiers = max(1, rounds // 2)
    tiers = {}
    for i, team in enumerate(order):
        draft_round = i // n_players + 1                # 1-indexed round
        tiers[team] = min((draft_round + 1) // 2, n_tiers)
    return tiers


def score_team(team: str, tiers: dict[str, int], stages: set) -> dict:
    """Score one team under the Sim 2 policy, returning a component breakdown."""
    w = d = goal_base = result_bonus = goal_bonus = 0
    for m in load_matches():
        if m.stage not in stages or team not in (m.team1, m.team2):
            continue
        g1, g2 = reg90(m)
        gf, ga = (g1, g2) if team == m.team1 else (g2, g1)
        opp = m.team2 if team == m.team1 else m.team1
        gap = min(max(tiers[team] - tiers[opp], 0), 2)   # tiers above opponent
        won_or_drew = gf >= ga

        goal_base += gf
        if gf > ga:
            w += 1
        elif gf == ga:
            d += 1
        if won_or_drew:                       # bonus only when you take a result
            result_bonus += gap
            goal_bonus += gf * gap

    result_base = 3 * w + d
    return {
        "W": w, "D": d,
        "result_base": result_base, "result_bonus": result_bonus,
        "goal_base": goal_base, "goal_bonus": goal_bonus,
        "pts": result_base + result_bonus + goal_base + goal_bonus,
    }


def table(rosters: dict[int, list[str]], tiers: dict[str, int],
          stages: set, title: str) -> None:
    print("=" * 64)
    print(title)
    print("=" * 64)
    player_total: dict[int, int] = {}
    for player, teams in rosters.items():
        rows = [(t, score_team(t, tiers, stages)) for t in teams]
        total = sum(s["pts"] for _, s in rows)
        base = sum(s["result_base"] + s["goal_base"] for _, s in rows)
        bonus = sum(s["result_bonus"] + s["goal_bonus"] for _, s in rows)
        player_total[player] = total
        print(f"\nPlayer {player}  —  {total} pts   (base {base} + bonus {bonus})")
        print(f"   {'team':<15}{'T':>2}{'base':>6}{'bonus':>7}{'pts':>5}")
        for t, s in sorted(rows, key=lambda r: r[1]["pts"], reverse=True):
            b = s["result_base"] + s["goal_base"]
            bo = s["result_bonus"] + s["goal_bonus"]
            print(f"   {t:<15}{tiers[t]:>2}{b:>6}{bo:>7}{s['pts']:>5}")

    print("\n   --- standings ---")
    for pos, (p, tot) in enumerate(
        sorted(player_total.items(), key=lambda x: x[1], reverse=True), 1
    ):
        print(f"   {pos}. Player {p}   {tot} pts")
    print()


if __name__ == "__main__":
    rosters = snake_draft(n_players=5, rounds=6)
    tiers = build_tiers(n_players=5, rounds=6)
    table(rosters, tiers, GROUP_STAGE, "AFTER GROUP STAGE")
    table(rosters, tiers, ALL_STAGES, "AFTER FINAL (whole tournament)")
