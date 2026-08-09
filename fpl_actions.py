"""Authenticated FPL actions: log in, set the lineup, and make transfers.

The public FPL API is read-only without a session. Everything in this module
needs a logged-in cookie jar, obtained one of two ways:

1. ``login()`` drives a real browser via Playwright against
   ``users.premierleague.com``. This is the hands-off route but only works
   where that host is reachable (i.e. on your own machine).
2. ``cookies_from_string()`` takes cookies you copied out of your own browser.
   Use this when the login host is blocked but
   ``fantasy.premierleague.com`` is not.

Both produce a plain ``{name: value}`` dict that ``FPLTeam`` accepts.

Typical use::

    cookies = load_cookies()                    # or await login(email, pw)
    async with FPLTeam(cookies) as team:
        state = await team.get_my_team()
        await team.set_lineup(picks, confirm=True)

Every mutating call defaults to ``confirm=False``, which prints the payload
and returns it without sending anything. Pass ``confirm=True`` to actually
apply the change.
"""

import asyncio
import json
import os

import aiohttp

from constants import API_URLS, TEAM_ID
from utils import check_response, fetch

# The old users.premierleague.com login was retired - the host no longer even
# resolves. Logging in now means the myPL OAuth flow on
# account.premierleague.com, which the Fantasy site kicks off itself. Rather
# than hand-building an authorize URL (it carries a PKCE challenge and state
# the app generates), we click the site's own "Log in" button and follow it.
FANTASY_URL = "https://fantasy.premierleague.com"
LOGIN_START_URL = f"{FANTASY_URL}/my-team"
AUTHORIZE_URL = "https://account.premierleague.com/as/authorize"

# Email/password, written by hand or by an earlier setup script.
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), ".credentials")

# Session cookies. Deliberately a *different* file from CREDENTIALS_PATH:
# saving cookies over your stored email and password would lock you out of
# the automatic login. Both are gitignored (".credentials", "*.credentials").
SESSION_PATH = os.path.join(os.path.dirname(__file__), ".session.credentials")

# Cookie domains worth keeping. The myPL migration changed the session cookie
# names, so we no longer filter by name - keeping every premierleague.com
# cookie is what makes this survive the next rename. Whether the session
# actually works is settled by calling the API, not by looking for a name.
COOKIE_DOMAINS = ("premierleague.com",)

# Chip identifiers as the API expects them.
CHIPS = {
    "bench_boost": "bboost",
    "free_hit": "freehit",
    "triple_captain": "3xc",
    "wildcard": "wildcard",
}

SQUAD_SIZE = 15
STARTING_XI = 11

# element_type -> (min, max) allowed in the starting XI.
FORMATION_LIMITS = {
    1: (1, 1),   # Goalkeeper
    2: (3, 5),   # Defender
    3: (0, 5),   # Midfielder
    4: (1, 3),   # Forward
}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

async def login(email, password, headless=True, timeout=60000):
    """Logs in with a real browser and returns the session cookies.

    Drives the myPL OAuth flow: open the Fantasy site, click its "Log in"
    button, fill the form on ``account.premierleague.com``, and let the
    redirect back to Fantasy establish the session. Runs headless - the
    login page serves no captcha to a scripted browser.

    Requires Playwright (``pip install playwright`` and
    ``playwright install chromium``).

    :param str email: myPL account email.
    :param str password: myPL account password.
    :param bool headless: Run without a visible window. Set False to watch
        the flow or to complete an unexpected challenge by hand.
    :param int timeout: Navigation timeout in milliseconds.
    :return: Cookie name/value pairs for the logged-in session.
    :rtype: dict
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(LOGIN_START_URL, timeout=timeout,
                        wait_until="domcontentloaded")
        await _dismiss_consent(page)

        # The Fantasy header's "Log in" hands off to the myPL authorize URL,
        # PKCE parameters and all.
        try:
            await page.get_by_role(
                "button", name="Log in", exact=True).first.click(timeout=20000)
            await page.wait_for_url(f"{AUTHORIZE_URL}**", timeout=timeout)
        except Exception:
            raise Exception(
                f"Could not reach the myPL login page from {LOGIN_START_URL}. "
                f"Stuck at {page.url}. Retry with headless=False to watch it."
            )

        await _dismiss_consent(page)
        await page.fill("#username", email)
        await page.fill("#password", password)
        await page.click("#btnSignIn")

        # A successful login bounces back to fantasy.premierleague.com with
        # an authorization code the site exchanges for a session.
        try:
            await page.wait_for_url(f"{FANTASY_URL}/**", timeout=timeout)
        except Exception:
            raise Exception(
                f"Login was not accepted: {await _login_error(page)} "
                f"(still on {page.url[:80]}). Check the email and password "
                f"in {CREDENTIALS_PATH}, or retry with headless=False."
            )

        # Let the code-for-session exchange finish before taking cookies.
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        cookies = {
            c["name"]: c["value"] for c in await context.cookies()
            if any(c["domain"].endswith(d) for d in COOKIE_DOMAINS)
        }
        await browser.close()

    if not cookies:
        raise Exception("Logged in but no premierleague.com cookies were set")

    return cookies


async def _dismiss_consent(page):
    """Clicks away the OneTrust banner, which overlays the login form."""
    try:
        await page.click("#onetrust-accept-btn-handler", timeout=5000)
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def _login_error(page):
    """Best-effort read of the error the login form is showing."""
    try:
        text = await page.evaluate("() => document.body.innerText")
    except Exception:
        return "no error message could be read"
    for line in text.splitlines():
        line = line.strip()
        if "invalid" in line.lower() or "incorrect" in line.lower():
            return line
    return "no error message on the page"


def cookies_from_string(cookie_header):
    """Parses cookies copied out of a browser into a dict.

    Accepts the raw ``Cookie:`` header form, e.g. ``"pl_profile=abc;
    sessionid=def"``. Every cookie is kept: which ones carry the myPL session
    is not something to guess at, and a session that works is proved by
    calling the API, not by spotting a name.

    :param str cookie_header: Semicolon-separated cookie string.
    :rtype: dict
    """
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()

    if not cookies:
        raise Exception(
            "No cookies found in that string. Copy the whole Cookie header "
            "from DevTools > Network on fantasy.premierleague.com."
        )

    return cookies


def save_cookies(cookies, path=SESSION_PATH):
    """Writes cookies to a gitignored file with owner-only permissions."""
    with open(path, "w") as f:
        json.dump(cookies, f)
    os.chmod(path, 0o600)
    return path


def load_cookies(path=SESSION_PATH):
    """Reads cookies previously saved by :func:`save_cookies`."""
    if not os.path.exists(path):
        raise Exception(
            f"No saved session at {path}. Run login() or "
            f"cookies_from_string() first, then save_cookies()."
        )
    with open(path) as f:
        return json.load(f)


def load_credentials(path=CREDENTIALS_PATH):
    """Reads the stored email and password, if there are any.

    Expects ``{"username": ..., "password": ...}``. Returns ``(None, None)``
    when the file is absent or unreadable, so callers can fall back to
    prompting.

    :rtype: tuple
    """
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as f:
            stored = json.load(f)
    except (ValueError, OSError):
        return None, None
    return stored.get("username"), stored.get("password")


# --------------------------------------------------------------------------
# Squad validation
# --------------------------------------------------------------------------

def validate_lineup(picks, players_by_id):
    """Checks a lineup against the FPL formation rules.

    Catches the mistakes that make the API reject a save, before it is sent.

    :param list picks: Picks in the payload shape, each with ``element``,
        ``position``, ``is_captain`` and ``is_vice_captain``.
    :param dict players_by_id: Maps player id to a dict with ``element_type``.
    :return: Human-readable problems. Empty means the lineup is legal.
    :rtype: list
    """
    errors = []

    if len(picks) != SQUAD_SIZE:
        errors.append(f"Squad has {len(picks)} players, expected {SQUAD_SIZE}")

    positions = [p["position"] for p in picks]
    if sorted(positions) != list(range(1, len(picks) + 1)):
        errors.append(
            f"Positions must be 1..{len(picks)} with no gaps or duplicates, "
            f"got {sorted(positions)}"
        )

    unknown = [p["element"] for p in picks if p["element"] not in players_by_id]
    if unknown:
        errors.append(f"Unknown player ids: {unknown}")
        return errors

    starters = [p for p in picks if p["position"] <= STARTING_XI]
    if len(starters) != STARTING_XI:
        errors.append(
            f"Starting XI has {len(starters)} players, expected {STARTING_XI}")

    counts = {}
    for pick in starters:
        element_type = players_by_id[pick["element"]]["element_type"]
        counts[element_type] = counts.get(element_type, 0) + 1

    for element_type, (low, high) in FORMATION_LIMITS.items():
        count = counts.get(element_type, 0)
        if not low <= count <= high:
            errors.append(
                f"Starting XI has {count} of position type {element_type}, "
                f"must be between {low} and {high}"
            )

    # The reserve keeper must sit at position 12; outfield subs follow.
    bench = sorted((p for p in picks if p["position"] > STARTING_XI),
                   key=lambda p: p["position"])
    if bench:
        first = players_by_id[bench[0]["element"]]["element_type"]
        if first != 1:
            errors.append(
                "Bench position 12 must be the reserve goalkeeper")

    captains = [p for p in picks if p.get("is_captain")]
    vices = [p for p in picks if p.get("is_vice_captain")]

    if len(captains) != 1:
        errors.append(f"Expected exactly 1 captain, got {len(captains)}")
    if len(vices) != 1:
        errors.append(f"Expected exactly 1 vice-captain, got {len(vices)}")
    if captains and vices and captains[0]["element"] == vices[0]["element"]:
        errors.append("Captain and vice-captain must be different players")
    for role, chosen in (("Captain", captains), ("Vice-captain", vices)):
        if chosen and chosen[0]["position"] > STARTING_XI:
            errors.append(f"{role} must be in the starting XI")

    return errors


def build_picks(starting_xi, bench, captain, vice_captain):
    """Builds the picks payload from ordered player id lists.

    :param list starting_xi: 11 player ids. Order is not significant beyond
        the goalkeeper, which must come first.
    :param list bench: 4 player ids in substitution priority, reserve
        goalkeeper first.
    :param int captain: Player id of the captain.
    :param int vice_captain: Player id of the vice-captain.
    :rtype: list
    """
    picks = []
    for index, element in enumerate(list(starting_xi) + list(bench), start=1):
        picks.append({
            "element": element,
            "position": index,
            "is_captain": element == captain,
            "is_vice_captain": element == vice_captain,
        })
    return picks


# --------------------------------------------------------------------------
# Team management
# --------------------------------------------------------------------------

class FPLTeam:
    """Authenticated access to a single FPL squad.

    All writes are dry runs unless ``confirm=True`` is passed, so a mistaken
    call prints its payload instead of changing the team.
    """

    def __init__(self, cookies, team_id=TEAM_ID):
        """
        :param dict cookies: Session cookies from :func:`login` or
            :func:`cookies_from_string`.
        :param int team_id: FPL entry id. Defaults to ``constants.TEAM_ID``.
        """
        self.cookies = cookies
        self.team_id = team_id
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(cookies=self.cookies)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()
        self.session = None

    def _headers(self, referer):
        """Headers FPL requires on authenticated writes."""
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
        if "csrftoken" in self.cookies:
            headers["X-CSRFToken"] = self.cookies["csrftoken"]
        return headers

    async def get_my_team(self):
        """Returns the current squad, bank, and chip availability.

        The response includes ``picks`` (with ``selling_price`` and
        ``purchase_price`` per player), ``chips``, and ``transfers``.

        :rtype: dict
        """
        url = API_URLS["user_team"].format(self.team_id)
        async with self.session.get(url, headers=self._headers(
                "https://fantasy.premierleague.com/my-team")) as response:
            if response.status == 403:
                raise Exception(
                    "403 from my-team: the session is not valid. Cookies "
                    "expire, so refresh them and try again."
                )
            await check_response(response)
            return await response.json()

    async def is_logged_in(self):
        """Checks the session by asking for the squad. Cheap and definitive.

        :rtype: bool
        """
        try:
            await self.get_my_team()
        except Exception:
            return False
        return True

    async def get_current_gameweek(self):
        """Returns the id of the next gameweek to be played.

        :rtype: int
        """
        static = await fetch(self.session, API_URLS["static"])
        upcoming = next(
            (e for e in static["events"] if e["is_next"]), None)
        if upcoming is None:
            upcoming = next(e for e in static["events"] if e["is_current"])
        return upcoming["id"]

    async def set_lineup(self, picks, chip=None, confirm=False):
        """Saves the starting XI, bench order, captain and vice-captain.

        Chips saved here are ``bench_boost`` and ``triple_captain``.
        ``wildcard`` and ``free_hit`` are played through
        :meth:`make_transfers` instead.

        :param list picks: Output of :func:`build_picks`.
        :param str chip: Optional chip key from :data:`CHIPS`.
        :param bool confirm: Send the request. False prints and returns the
            payload without contacting FPL.
        :rtype: dict
        """
        if chip is not None and chip not in CHIPS:
            raise ValueError(
                f"Unknown chip {chip!r}, expected one of {sorted(CHIPS)}")

        payload = {
            "chip": CHIPS[chip] if chip else None,
            "picks": [
                {
                    "element": p["element"],
                    "position": p["position"],
                    "is_captain": p["is_captain"],
                    "is_vice_captain": p["is_vice_captain"],
                }
                for p in sorted(picks, key=lambda p: p["position"])
            ],
        }

        if not confirm:
            print("DRY RUN - set_lineup would POST:")
            print(json.dumps(payload, indent=2))
            return payload

        url = API_URLS["user_team"].format(self.team_id)
        headers = self._headers("https://fantasy.premierleague.com/my-team")
        async with self.session.post(
                url, data=json.dumps(payload), headers=headers) as response:
            await check_response(response)

        return payload

    async def make_transfers(self, transfers, gameweek=None, chip=None,
                             confirm=False):
        """Transfers players in and out.

        Prices matter: FPL validates ``selling_price`` against what you
        actually paid, so take them from :meth:`get_my_team` rather than from
        the public player list. :meth:`plan_transfers` does that for you.

        :param list transfers: Dicts with ``element_in``, ``element_out``,
            ``purchase_price`` and ``selling_price``.
        :param int gameweek: Gameweek to transfer into. Defaults to the next
            one.
        :param str chip: ``wildcard`` or ``free_hit`` to make them free.
        :param bool confirm: Send the request. False prints and returns the
            payload without contacting FPL.
        :rtype: dict
        """
        if chip is not None and chip not in ("wildcard", "free_hit"):
            raise ValueError(
                "Only wildcard and free_hit apply to transfers; bench_boost "
                "and triple_captain are played via set_lineup()"
            )

        if gameweek is None:
            gameweek = await self.get_current_gameweek()

        payload = {
            "chip": CHIPS[chip] if chip else None,
            "entry": self.team_id,
            "event": gameweek,
            "transfers": transfers,
            "confirmed": True,
        }

        if not confirm:
            print("DRY RUN - make_transfers would POST:")
            print(json.dumps(payload, indent=2))
            return payload

        headers = self._headers("https://fantasy.premierleague.com/transfers")
        async with self.session.post(
                API_URLS["transfers"], data=json.dumps(payload),
                headers=headers) as response:
            await check_response(response)

        return payload

    async def plan_transfers(self, moves, players_by_id):
        """Turns ``(out_id, in_id)`` pairs into a priced transfers list.

        Selling prices come from the live squad, which is the only place the
        sell-on fee is accounted for.

        :param list moves: ``(element_out, element_in)`` id pairs.
        :param dict players_by_id: Maps player id to a dict with ``now_cost``.
        :rtype: list
        """
        state = await self.get_my_team()
        selling = {p["element"]: p["selling_price"] for p in state["picks"]}

        transfers = []
        for element_out, element_in in moves:
            if element_out not in selling:
                raise Exception(
                    f"Player {element_out} is not in the current squad")
            if element_in not in players_by_id:
                raise Exception(f"Unknown incoming player {element_in}")

            cost = players_by_id[element_in]["now_cost"]
            transfers.append({
                "element_in": element_in,
                "element_out": element_out,
                "purchase_price": cost,
                "selling_price": selling[element_out],
            })

        return transfers


async def get_session(team_id=TEAM_ID, force_login=False):
    """Returns cookies for a session that is known to work.

    Reuses the saved session when it is still valid and logs in again when it
    is not, so scheduled runs need no attention. Requires an email and
    password in ``.credentials`` for the re-login to be automatic.

    :param int team_id: Entry id used for the validity check.
    :param bool force_login: Skip the cached session and log in fresh.
    :rtype: dict
    """
    if not force_login:
        try:
            cookies = load_cookies()
        except Exception:
            cookies = None

        if cookies:
            async with FPLTeam(cookies, team_id) as team:
                if await team.is_logged_in():
                    return cookies

    email, password = load_credentials()
    if not email or not password:
        raise Exception(
            f"The saved session is gone or expired and {CREDENTIALS_PATH} has "
            f"no email/password to log in with. Add them, or refresh the "
            f"session by hand with cookies_from_string()."
        )

    cookies = await login(email, password)
    save_cookies(cookies)
    return cookies


async def _main():
    """Saves a session so later runs can skip the browser."""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cookie", help="Cookie header copied from a browser")
    parser.add_argument("--email", help="Log in with Playwright instead")
    parser.add_argument("--login", action="store_true",
                        help="Force a fresh login with the email and "
                             "password in .credentials")
    parser.add_argument("--show-browser", action="store_true",
                        help="Run the browser visibly to watch the flow")
    args = parser.parse_args()

    if not any((args.cookie, args.email, args.login)):
        # No arguments: reuse the saved session, logging in only if needed.
        cookies = await get_session()
    elif args.cookie:
        cookies = cookies_from_string(args.cookie)
    else:
        stored_email, stored_password = load_credentials()
        email = args.email or stored_email
        if not email:
            parser.error("no email given and none stored in .credentials")
        # Only reuse the stored password if it belongs to that email.
        password = stored_password if email == stored_email else None
        if not password:
            password = getpass.getpass("FPL password: ")
        cookies = await login(email, password,
                              headless=not args.show_browser)

    save_cookies(cookies)
    print(f"Session saved to {SESSION_PATH}")

    async with FPLTeam(cookies) as team:
        state = await team.get_my_team()
        bank = state["transfers"]["bank"] / 10
        value = state["transfers"]["value"] / 10
        print(f"Squad of {len(state['picks'])}, bank £{bank}m, "
              f"value £{value}m, "
              f"{state['transfers']['limit']} free transfer(s)")


if __name__ == "__main__":
    asyncio.run(_main())
