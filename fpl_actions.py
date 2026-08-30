"""Authenticated FPL actions: log in, set the lineup, and make transfers.

The public FPL API is read-only without a session. Everything in this module
needs an OAuth access token, sent as an ``X-Api-Authorization: Bearer ...``
header. Cookies play no part - a request with every cookie the site sets and
no token is rejected with 403, and a request with the token and no cookies at
all succeeds.

Two ways to get a session:

1. ``login()`` drives a real browser via Playwright through the myPL OAuth
   flow and reads the token the site stores. Hands-off, and works headless.
2. ``session_from_token()`` takes an access token you copied out of your own
   browser. Use it when a browser cannot be launched.

Both produce a session dict that ``FPLTeam`` accepts.

Typical use::

    session = await get_session()               # logs in only if it must
    async with FPLTeam(session) as team:
        state = await team.get_my_team()
        await team.set_lineup(picks, confirm=True)

Every mutating call defaults to ``confirm=False``, which prints the payload
and returns it without sending anything. Pass ``confirm=True`` to actually
apply the change.
"""

import asyncio
import json
import os
import time

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

# Saved session. Deliberately a *different* file from CREDENTIALS_PATH:
# writing the session over your stored email and password would lock you out
# of the automatic login. Both are gitignored (".credentials",
# "*.credentials").
SESSION_PATH = os.path.join(os.path.dirname(__file__), ".session.credentials")

# Root-free Chromium runtime libraries. On a box where we cannot ``sudo
# playwright install-deps``, the needed Ubuntu packages are downloaded with
# ``apt-get download`` (no privileges required) and unpacked under
# ``.chromium-libs/root``; ``_browser_env()`` then points the browser at them.
# Absent on machines with the system packages installed, and harmless there.
CHROMIUM_LIBS_DIR = os.path.join(os.path.dirname(__file__), ".chromium-libs", "root")


def _browser_env():
    """Environment for the Chromium subprocess, adding the local libraries in
    :data:`CHROMIUM_LIBS_DIR` when that directory exists.

    :return: Environment mapping to pass to ``chromium.launch``.
    :rtype: dict
    """
    env = dict(os.environ)
    if not os.path.isdir(CHROMIUM_LIBS_DIR):
        return env
    lib_dirs = sorted({
        dirpath for dirpath, _, files in os.walk(CHROMIUM_LIBS_DIR)
        if any(".so" in f for f in files)
    })
    env["LD_LIBRARY_PATH"] = ":".join(lib_dirs + [env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
    fonts = os.path.join(CHROMIUM_LIBS_DIR, "etc", "fonts")
    if os.path.isdir(fonts):
        env["FONTCONFIG_PATH"] = fonts
    return env

# The Fantasy site's OAuth client. Its access token is kept in localStorage
# under "oidc.user:<issuer>:<client id>".
OIDC_CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"
OIDC_STORAGE_PREFIX = "oidc.user:"

# The API reads the bearer token from this header. Plain "Authorization"
# also works today, but this is the one the site itself sends.
AUTH_HEADER = "X-Api-Authorization"

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
    """Logs in with a real browser and returns a session dict.

    Drives the myPL OAuth flow: open the Fantasy site, click its "Log in"
    button, fill the form on ``account.premierleague.com``, and let the
    redirect back to Fantasy complete the token exchange. The access token
    is then read out of the page's localStorage. Runs headless - the login
    page serves no captcha to a scripted browser.

    Requires Playwright (``pip install playwright`` and
    ``playwright install chromium``).

    :param str email: myPL account email.
    :param str password: myPL account password.
    :param bool headless: Run without a visible window. Set False to watch
        the flow or to complete an unexpected challenge by hand.
    :param int timeout: Navigation timeout in milliseconds.
    :return: ``access_token``, ``refresh_token`` and ``expires_at``.
    :rtype: dict
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, env=_browser_env())
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
        # Poll rather than wait_for_url alone: the form's error banner is
        # transient, so by the time a long wait expires the reason is gone.
        deadline = time.time() + timeout / 1000
        error = None
        while not page.url.startswith(FANTASY_URL):
            error = await _login_error(page)
            if error or time.time() > deadline:
                raise Exception(
                    f"Login was not accepted: "
                    f"{error or 'no error message on the page'} "
                    f"(still on {page.url[:80]}). Check the email and "
                    f"password in {CREDENTIALS_PATH}, or retry with "
                    f"headless=False."
                )
            await page.wait_for_timeout(500)

        # The token appears in localStorage once the redirect has been
        # exchanged for it, which happens a moment after the navigation.
        try:
            await page.wait_for_function(
                f"""() => Object.keys(window.localStorage)
                        .some(k => k.startsWith({OIDC_STORAGE_PREFIX!r}))""",
                timeout=30000)
        except Exception:
            raise Exception(
                "Logged in but no OAuth token appeared in localStorage. The "
                "site may have changed how it stores the session."
            )

        stored = await page.evaluate(
            f"""() => {{
                const key = Object.keys(window.localStorage)
                    .find(k => k.startsWith({OIDC_STORAGE_PREFIX!r}));
                return key ? window.localStorage.getItem(key) : null;
            }}""")
        await browser.close()

    return _session_from_oidc(json.loads(stored))


def _session_from_oidc(oidc):
    """Picks the fields worth keeping out of the site's OIDC blob.

    ``id_token`` is deliberately not used: the API rejects it with
    "Audience doesn't match". ``access_token`` is the one that authenticates.
    """
    token = oidc.get("access_token")
    if not token:
        raise Exception("The stored OAuth entry has no access_token")

    return {
        "access_token": token,
        "refresh_token": oidc.get("refresh_token"),
        "expires_at": oidc.get("expires_at"),
    }


def session_from_token(access_token):
    """Builds a session from a token copied out of a browser.

    Find it in DevTools > Application > Local Storage on
    fantasy.premierleague.com, under the ``oidc.user:...`` key, as the
    ``access_token`` field.

    :param str access_token: The bearer token, without the "Bearer " prefix.
    :rtype: dict
    """
    token = access_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise Exception("No access token given")
    return {"access_token": token, "refresh_token": None, "expires_at": None}


async def _dismiss_consent(page):
    """Clicks away the OneTrust banner, which overlays the login form."""
    try:
        await page.click("#onetrust-accept-btn-handler", timeout=5000)
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def _login_error(page):
    """Best-effort read of the error the login form is showing.

    :return: The error line, or ``None`` when the form shows none.
    :rtype: str or None
    """
    # textContent, not innerText: innerText needs a computed layout and reads
    # as empty in headless Chromium while the form is still visible.
    try:
        text = await page.evaluate("() => document.body.textContent")
    except Exception:
        return None
    text = " ".join(text.split()).lower()
    for marker in ("invalid username", "invalid", "incorrect", "locked", "too many"):
        i = text.find(marker)
        if i >= 0:
            return text[i:i + 60].split("email address")[0].strip()
    return None


def save_session(session, path=SESSION_PATH):
    """Writes a session to a gitignored file with owner-only permissions."""
    with open(path, "w") as f:
        json.dump(session, f)
    os.chmod(path, 0o600)
    return path


def load_session(path=SESSION_PATH):
    """Reads a session previously saved by :func:`save_session`."""
    if not os.path.exists(path):
        raise Exception(
            f"No saved session at {path}. Run login() or "
            f"session_from_token() first, then save_session()."
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

    def __init__(self, session, team_id=TEAM_ID):
        """
        :param dict session: Session from :func:`login`, :func:`get_session`
            or :func:`session_from_token`. A bare token string is accepted
            too.
        :param int team_id: FPL entry id. Defaults to ``constants.TEAM_ID``.
        """
        if isinstance(session, str):
            session = session_from_token(session)
        if not isinstance(session, dict) or "access_token" not in session:
            raise ValueError(
                "FPLTeam needs a session dict with an access_token. Cookies "
                "no longer authenticate the FPL API - use get_session()."
            )

        self.auth = session
        self.team_id = team_id
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()
        self.session = None

    def _headers(self, referer):
        """Headers FPL requires on authenticated calls.

        The bearer token is what actually authenticates. No CSRF token is
        involved: the API is token-authenticated, not cookie-authenticated,
        so there is no cookie for a CSRF check to protect.
        """
        return {
            AUTH_HEADER: f"Bearer {self.auth['access_token']}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }

    async def get_my_team(self):
        """Returns the current squad, bank, and chip availability.

        The response includes ``picks`` (with ``selling_price`` and
        ``purchase_price`` per player), ``chips``, and ``transfers``.

        :rtype: dict
        """
        url = API_URLS["user_team"].format(self.team_id)
        async with self.session.get(url, headers=self._headers(
                "https://fantasy.premierleague.com/my-team")) as response:
            if response.status in (401, 403):
                raise Exception(
                    f"{response.status} from my-team: the access token is "
                    f"missing, expired or not valid for entry "
                    f"{self.team_id}. get_session() refreshes it by logging "
                    f"in again."
                )
            if response.status == 404:
                raise Exception(
                    f"404 from my-team: entry {self.team_id} does not exist "
                    f"or is not managed by this account. Check TEAM_ID in "
                    f"constants.py."
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
    """Returns a session that is known to work.

    Reuses the saved session while it is still valid and logs in again when
    it is not, so scheduled runs need no attention. Requires an email and
    password in ``.credentials`` for the re-login to be automatic.

    Access tokens are short-lived - roughly an hour - so most runs that are
    more than an hour apart will log in again. That takes about 20 seconds.

    :param int team_id: Entry id used for the validity check.
    :param bool force_login: Skip the cached session and log in fresh.
    :rtype: dict
    """
    if not force_login:
        try:
            session = load_session()
        except Exception:
            session = None

        # A session saved by an older version holds cookies rather than a
        # token; treat anything unrecognised as simply absent.
        if (isinstance(session, dict) and session.get("access_token")
                and not _is_expired(session)):
            async with FPLTeam(session, team_id) as team:
                if await team.is_logged_in():
                    return session

    email, password = load_credentials()
    if not email or not password:
        raise Exception(
            f"The saved session is gone or expired and {CREDENTIALS_PATH} has "
            f"no email/password to log in with. Add them, or supply a token "
            f"by hand with session_from_token()."
        )

    session = await login(email, password)
    save_session(session)
    return session


def _is_expired(session, margin=60):
    """True when the token has expired, or is about to.

    Unknown expiry counts as not expired - the API call that follows will
    settle it either way.
    """
    expires_at = session.get("expires_at")
    if not expires_at:
        return False
    return time.time() > expires_at - margin


async def _main():
    """Saves a session so later runs can skip the browser."""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="Access token copied from a browser")
    parser.add_argument("--email", help="Log in as this address")
    parser.add_argument("--login", action="store_true",
                        help="Force a fresh login with the email and "
                             "password in .credentials")
    parser.add_argument("--show-browser", action="store_true",
                        help="Run the browser visibly to watch the flow")
    args = parser.parse_args()

    if not any((args.token, args.email, args.login)):
        # No arguments: reuse the saved session, logging in only if needed.
        session = await get_session()
    elif args.token:
        session = session_from_token(args.token)
    else:
        stored_email, stored_password = load_credentials()
        email = args.email or stored_email
        if not email:
            parser.error("no email given and none stored in .credentials")
        # Only reuse the stored password if it belongs to that email.
        password = stored_password if email == stored_email else None
        if not password:
            password = getpass.getpass("FPL password: ")
        session = await login(email, password,
                              headless=not args.show_browser)

    save_session(session)
    print(f"Session saved to {SESSION_PATH}")

    async with FPLTeam(session) as team:
        state = await team.get_my_team()
        transfers = state["transfers"]
        # Before the first deadline transfers are unlimited and limit is null.
        limit = transfers["limit"]
        free = transfers["status"] if limit is None else f"{limit} free"
        print(f"Entry {team.team_id}: squad of {len(state['picks'])}, "
              f"bank £{transfers['bank'] / 10}m, "
              f"value £{transfers['value'] / 10}m, "
              f"{free} transfer(s)")


if __name__ == "__main__":
    asyncio.run(_main())
