"""Snake-draft simulation for the WC 2022 draft game.

A snake (serpentine) draft reverses the pick order every round, so over a full
round each player gets one early and one late pick. With the "best available by
FIFA rank" strategy the draft is deterministic: picks simply run in rank order,
zig-zagging across players.

    from draft import snake_draft, score_rosters
    rosters = snake_draft(n_players=5, rounds=6)
    score_rosters(rosters)
"""

from __future__ import annotations

from wc2022_data import load_rankings, score_team


def snake_draft(n_players: int = 5, rounds: int = 6,
                strategy: str = "best_rank") -> dict[int, list[str]]:
    """Run a snake draft and return {player_index: [teams]} (1-indexed players).

    strategy="best_rank": each pick takes the highest FIFA-ranked team still
    available (the only strategy implemented for now).
    """
    ranks = load_rankings()
    if strategy != "best_rank":
        raise NotImplementedError(strategy)
    # Best-available pool, strongest first.
    pool = sorted(ranks, key=lambda t: ranks[t]["fifa_rank"])

    rosters: dict[int, list[str]] = {p: [] for p in range(1, n_players + 1)}
    pick_log: list[tuple] = []
    idx = 0
    for rnd in range(rounds):
        order = range(1, n_players + 1)
        if rnd % 2 == 1:               # reverse on odd rounds -> "snake"
            order = reversed(list(order))
        for player in order:
            if idx >= len(pool):
                break
            team = pool[idx]
            rosters[player].append(team)
            pick_log.append((idx + 1, rnd + 1, player, team, ranks[team]["fifa_rank"]))
            idx += 1

    snake_draft.last_pick_log = pick_log          # stash for inspection
    snake_draft.last_undrafted = pool[idx:]
    return rosters


def score_rosters(rosters: dict[int, list[str]], points: dict = None) -> None:
    """Print each roster, per-team scores, and the league table."""
    ranks = load_rankings()

    print("=" * 64)
    print("ROSTERS & SCORES")
    print("=" * 64)
    totals: dict[int, int] = {}
    for player, teams in rosters.items():
        print(f"\nPlayer {player}:")
        ptotal = 0
        for t in teams:
            sc = score_team(t, points)["TOTAL"]
            ptotal += sc
            print(f"   {t:<16} (rank {ranks[t]['fifa_rank']:>2})  {sc:>4} pts")
        totals[player] = ptotal
        print(f"   {'TOTAL':<16}           {ptotal:>4} pts")

    print("\n" + "=" * 64)
    print("LEAGUE TABLE")
    print("=" * 64)
    for pos, (player, tot) in enumerate(
        sorted(totals.items(), key=lambda x: x[1], reverse=True), 1
    ):
        print(f"   {pos}. Player {player:<2}  {tot:>4} pts")


if __name__ == "__main__":
    rosters = snake_draft(n_players=5, rounds=6)

    print("DRAFT ORDER (snake; best available by FIFA rank)")
    print("-" * 64)
    print(f"{'#':>3} {'Rnd':>3}  {'Player':<7}{'Team':<16}{'Rank':>5}")
    for pick, rnd, player, team, rank in snake_draft.last_pick_log:
        print(f"{pick:>3} {rnd:>3}  P{player:<6}{team:<16}{rank:>5}")
    if snake_draft.last_undrafted:
        und = ", ".join(
            f"{t} ({load_rankings()[t]['fifa_rank']})"
            for t in snake_draft.last_undrafted
        )
        print(f"\nUndrafted: {und}")

    print()
    score_rosters(rosters)
