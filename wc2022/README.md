# WC 2022 draft game

Dataset and scoring engine for a World Cup 2022 draft competition.

## Data
- `matches.csv` — all 64 results (48 group + 16 knockout). Knockout shootouts are
  in `pens1`/`pens2`; `score1`/`score2` are the after-extra-time scoreline.
- `rankings.csv` — FIFA world ranking of all 32 teams as of 6 Oct 2022 (the last
  release before the tournament).

## Modules
- `wc2022_data.py` — loaders (`load_matches`, `load_rankings`) and `Match` helpers.
- `draft.py` — `snake_draft()` (best-available-by-rank serpentine draft).
- `simple_scoring.py` — the agreed scoring policy (below). Run it directly to
  print the post-group-stage and post-final standings with per-team breakdowns.

## Scoring policy ("Sim 2")
**Base** (per match, judged at **90 minutes** — extra time and penalty shootouts
are ignored, so the five shootout games count as draws):
- win **3**, draw **1**, **+1 per goal**.

**Underdog bonus** (additive, tier-based):
- Teams split into **3 tiers by draft round**: rounds 1-2 = Tier 1 (strongest),
  3-4 = Tier 2, 5-6 = Tier 3; undrafted teams fall in the bottom tier.
- **Win or draw** vs a team `N` tiers above you (`N` = 1 or 2): **+N** on the
  result **and +N per goal** scored in that match.
- A **defeat** scores base only (consolation goals still count 1 each, no bonus).

### Still open
- **Tier granularity** — 3 tiers has minor boundary cliffs (rank 11 vs 12). A
  finer "draft-round gap" alternative was discussed but not adopted.
- **Tie-breaker** — needed for level totals (e.g. group-stage ties). Not yet set.
