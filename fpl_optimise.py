"""Squad optimisation: minutes-aware projections, best XI, transfer search.

This sits on top of :mod:`fpl_projection`. That module answers "how good is
this player's rate, adjusted for the fixture". It deliberately leans on last
season's record while this season's minutes are thin, because a three-match
sample is noise. The cost of that choice is a specific, repeatable error:

    a player who has LOST his place still reads as a starter.

Harry Wilson (LEE) projected 6.41 for Gameweek 4 on his Fulham record while
playing 65, 45 and 17 minutes and scoring 4 points all season. The raw model
ranked him the third-best asset in the squad. He is a substitute.

So a projection is adjusted twice here:

* **quality** averages the model with FPL's own points-per-game / form blend.
  The blend is fixture-blind and short-sighted, but it is grounded in what
  actually happened, which is exactly what the model is short of early on.
* **play factor** scales by the share of the last two gameweeks' minutes the
  player actually got. Recent minutes, not season minutes: a player who
  missed Gameweek 1 injured and has started twice since (Mukiele) is nailed
  now, and season-long share would wrongly punish him.

The adjustment is deliberately gentle - a total bit-part player keeps half his
projection rather than losing all of it, because a substitute who comes on can
still return. It is a correction, not a veto.

Usage::

    from fpl_optimise import Squad
    sq = Squad(bootstrap, fixtures, history_past, gw_stats, squad_ids, selling)
    for p in sq.rated(4):
        print(p['name'], p['model'], p['adj'])
    xi, bench, total = sq.best_xi(4)
    for move in sq.best_transfers(4, bank=3, free=1):
        print(move)
"""

import itertools

from fpl_projection import Projector

# Minutes in the window used to judge whether a player is currently a starter.
RECENT_GWS = 2
FULL_MATCH = 90
# A player with no recent minutes keeps this share of his projection.
MIN_PLAY_FACTOR = 0.5
# Valid Fantasy Premier League formations, as (n_def, n_mid, n_fwd).
FORMATIONS = [(d, m, f)
              for d in range(3, 6) for m in range(2, 6) for f in range(1, 4)
              if d + m + f == 10]


def blended(element):
    """FPL's own points-per-game / form average.

    Coarse and fixture-blind, but it is a record of what the player has done
    rather than an estimate of what he might do.
    """
    return 0.5 * float(element["points_per_game"]) + 0.5 * float(element["form"])


class Squad:
    """A 15-man squad, rated and optimised for one gameweek."""

    def __init__(self, bootstrap, fixtures, history_past, gw_stats,
                 squad_ids, selling=None):
        """
        :param dict bootstrap: bootstrap-static payload.
        :param list fixtures: fixtures payload.
        :param dict history_past: ``{element_id: {last_points, last_minutes}}``.
        :param gw_stats: DataFrame of player_gameweek_stats.csv, or None to
            skip the play-factor adjustment entirely.
        :param squad_ids: the 15 element ids currently owned.
        :param dict selling: ``{element_id: selling_price}`` in 0.1m units.
            Falls back to ``now_cost``, which ignores the 50% sell-on fee, so
            pass real selling prices before planning transfers.
        """
        self.proj = Projector(bootstrap, fixtures, history_past)
        self.elements = {e["id"]: e for e in bootstrap["elements"]}
        self.squad_ids = list(squad_ids)
        self.selling = dict(selling or {})
        self._recent = self._recent_minutes(gw_stats)
        # rate() is called tens of thousands of times by the pair search.
        self._cache = {}

    # ---------------------------------------------------------------- ratings

    def _recent_minutes(self, gw_stats):
        """Minutes per element over the last RECENT_GWS completed gameweeks."""
        if gw_stats is None or gw_stats.empty:
            return {}
        rounds = sorted(gw_stats["round"].unique())[-RECENT_GWS:]
        window = gw_stats[gw_stats["round"].isin(rounds)]
        return {
            int(eid): (float(mins), len(rounds))
            for eid, mins in window.groupby("element")["minutes"].sum().items()
        }

    def play_factor(self, element_id):
        """MIN_PLAY_FACTOR..1.0 by share of recent minutes played."""
        got, n_gws = self._recent.get(element_id, (None, RECENT_GWS))
        if got is None:
            return 1.0
        share = min(1.0, got / (FULL_MATCH * n_gws))
        return MIN_PLAY_FACTOR + (1 - MIN_PLAY_FACTOR) * share

    def rate(self, element_id, gameweek):
        """A projection dict with ``model``, ``blend``, ``factor``, ``adj``."""
        hit = self._cache.get((element_id, gameweek), False)
        if hit is not False:
            return hit
        p = self._rate_uncached(element_id, gameweek)
        self._cache[(element_id, gameweek)] = p
        return p

    def _rate_uncached(self, element_id, gameweek):
        element = self.elements[element_id]
        p = self.proj.project(element, gameweek)
        if not p:
            return None
        p["model"] = p["proj"]
        p["blend"] = blended(element)
        p["factor"] = self.play_factor(element_id)
        p["adj"] = 0.5 * (p["model"] + p["blend"]) * p["factor"]
        p["sell"] = self.selling.get(element_id, element["now_cost"])
        p["cost"] = element["now_cost"]
        p["mins"] = element["minutes"]
        return p

    def rated(self, gameweek, ids=None):
        """Every squad member rated, best adjusted projection first."""
        out = [self.rate(i, gameweek) for i in (ids or self.squad_ids)]
        return sorted([p for p in out if p], key=lambda x: -x["adj"])

    # -------------------------------------------------------------- selection

    def best_xi(self, gameweek, ids=None, key="adj"):
        """Highest-scoring legal XI.

        :returns: ``(xi, bench, total)``. ``xi`` is goalkeeper first then
            defenders, midfielders, forwards; ``bench`` is the reserve keeper
            followed by the outfield subs in descending projection, which is
            the substitution order worth having.
        """
        players = self.rated(gameweek, ids)
        by_pos = {p: [] for p in ("GKP", "DEF", "MID", "FWD")}
        for p in players:
            by_pos[p["pos"]].append(p)
        for group in by_pos.values():
            group.sort(key=lambda x: -x[key])

        best = None
        for n_def, n_mid, n_fwd in FORMATIONS:
            if (len(by_pos["DEF"]) < n_def or len(by_pos["MID"]) < n_mid
                    or len(by_pos["FWD"]) < n_fwd or not by_pos["GKP"]):
                continue
            xi = ([by_pos["GKP"][0]] + by_pos["DEF"][:n_def]
                  + by_pos["MID"][:n_mid] + by_pos["FWD"][:n_fwd])
            total = sum(p[key] for p in xi)
            if best is None or total > best[2]:
                picked = {p["id"] for p in xi}
                bench = ([g for g in by_pos["GKP"][1:]]
                         + sorted((p for p in players
                                   if p["id"] not in picked and p["pos"] != "GKP"),
                                  key=lambda x: -x[key]))
                best = (xi, bench, total)
        return best

    # --------------------------------------------------------------- transfers

    def _candidates(self, gameweek, etype, max_cost, exclude, pool=6):
        """The ``pool`` best affordable replacements of one position.

        Pruning is what makes the pair search finish: without it the number of
        two-move combinations runs to millions. Only the top few buys per
        position can plausibly appear in a best pair, so the rest are dropped
        before combinations are formed.
        """
        out = []
        for element in self.elements.values():
            if element["element_type"] != etype or element["id"] in exclude:
                continue
            if element["now_cost"] > max_cost:
                continue
            p = self.rate(element["id"], gameweek)
            if not p or p["avail"] < 0.9:
                continue
            out.append(p)
        out.sort(key=lambda x: -x["adj"])
        return out[:pool]

    def best_transfers(self, gameweek, bank, free=1, horizon=4,
                       hit=4, top=10, max_moves=2):
        """Search single and double transfers, scored on best-XI improvement.

        A transfer is only worth what it adds to the eleven that actually
        plays, so every candidate is scored by rebuilding the best XI with the
        new player in the squad - not by comparing the two players directly.
        That is what stops the search from "upgrading" a substitute.

        :param bank: money in hand, 0.1m units.
        :param free: free transfers available; moves beyond this cost ``hit``.
        :param horizon: gameweeks beyond this one to weight at 0.6.
        :returns: list of dicts, best net gain first.
        """
        base_xi, _, base_total = self.best_xi(gameweek)
        base_fut = sum(self.proj.horizon(self.elements[p["id"]],
                                         gameweek + 1, gameweek + horizon)
                       for p in base_xi)
        owned = set(self.squad_ids)
        results = []

        singles = []
        for out_id in self.squad_ids:
            out_p = self.rate(out_id, gameweek)
            if not out_p:
                continue
            budget = out_p["sell"] + bank
            for in_p in self._candidates(gameweek, out_p["etype"], budget, owned):
                singles.append((out_p, in_p))

        for n_moves in range(1, max_moves + 1):
            for combo in itertools.combinations(singles, n_moves):
                outs = [c[0]["id"] for c in combo]
                ins = [c[1]["id"] for c in combo]
                if len(set(outs)) != n_moves or len(set(ins)) != n_moves:
                    continue
                spend = sum(c[1]["cost"] for c in combo)
                raise_ = sum(c[0]["sell"] for c in combo)
                if spend > raise_ + bank:
                    continue
                new_ids = [i for i in self.squad_ids if i not in outs] + ins
                if not self._legal_clubs(new_ids):
                    continue
                xi, bench, total = self.best_xi(gameweek, new_ids)
                fut = sum(self.proj.horizon(self.elements[p["id"]],
                                            gameweek + 1, gameweek + horizon)
                          for p in xi)
                cost = max(0, n_moves - free) * hit
                gain = (total - base_total) + 0.6 * (fut - base_fut) - cost
                results.append({
                    "moves": [(c[0], c[1]) for c in combo],
                    "n": n_moves, "cost": cost, "gain": gain,
                    "xi_gain": total - base_total,
                    "fut_gain": fut - base_fut,
                    "xi": xi, "bench": bench,
                })
        results.sort(key=lambda r: -r["gain"])
        return results[:top]

    def _legal_clubs(self, ids, limit=3):
        counts = {}
        for i in ids:
            team = self.elements[i]["team"]
            counts[team] = counts.get(team, 0) + 1
            if counts[team] > limit:
                return False
        return True
