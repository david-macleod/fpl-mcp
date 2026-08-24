"""Renders a gameweek team report as a self-contained HTML page.

The page is a single file with no external requests - fonts are system
stacks, the pitch and shirts are CSS - so it works on GitHub Pages, opened
from disk, or anywhere else.

Feed it a :class:`Report`; get back HTML::

    from fpl_report import Report, Player, Transfer, Chip, render

    html = render(Report(gameweek=1, ...))
    open("docs/index.html", "w").write(html)

Every section is optional except the header: pass an empty list and the
section disappears rather than rendering an empty shell.
"""

import html as _html
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Club colours, used for the shirt markers on the pitch.
TEAM_COLOURS = {
    "ARS": ("#EF0107", "#FFFFFF"), "AVL": ("#670E36", "#95BFE5"),
    "BOU": ("#DA291C", "#000000"), "BRE": ("#E30613", "#FFFFFF"),
    "BHA": ("#0057B8", "#FFCD00"), "CHE": ("#034694", "#FFFFFF"),
    "COV": ("#78D0F3", "#1F1F1F"), "CRY": ("#1B458F", "#C4122E"),
    "EVE": ("#003399", "#FFFFFF"), "FUL": ("#1A1A1A", "#FFFFFF"),
    "HUL": ("#F5A12D", "#1F1F1F"), "IPS": ("#3A64A3", "#FFFFFF"),
    "LEE": ("#1D428A", "#FFCD00"), "LIV": ("#C8102E", "#FFFFFF"),
    "MCI": ("#6CABDD", "#1C2C5B"), "MUN": ("#DA291C", "#FBE122"),
    "NEW": ("#241F20", "#FFFFFF"), "NFO": ("#DD0000", "#FFFFFF"),
    "TOT": ("#132257", "#FFFFFF"), "SUN": ("#EB172B", "#FFFFFF"),
}
DEFAULT_COLOURS = ("#2A3A4A", "#FFFFFF")

POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


# Guest managers for the Team sheet verdict, in rotation. Chosen for voices
# that are unmistakable in *writing* alone - cadence, vocabulary and verbal
# tics that survive without a voice or a face. A persona that only works as
# an impression of a sound does not belong here.
#
# Fictional characters are deliberately mixed in with real people: they are
# just as recognisable on the page and carry no question of putting words in
# a living person's mouth.
GUEST_MANAGERS = [
    ("Brian Blessed",
     "BELLOWING. Caps for emphasis, enormous appetite for everything, "
     "Shakespearean overstatement about a goalkeeper clean sheet."),
    ("Roy Keane",
     "Withering contempt in short sentences. 'Not good enough.' Refuses to "
     "be impressed by anything. Devastating when something goes well."),
    ("Alan Partridge",
     "Misplaced confidence, tortured sporting metaphors, needless "
     "specifics, sudden defensiveness. 'Back of the net.'"),
    ("Gordon Ramsay",
     "Kitchen fury. Food similes for bad decisions, RAW, escalating "
     "rhetorical questions. Keep it broadcast-clean."),
    ("David Attenborough",
     "Hushed wonder. The squad as a fragile ecosystem, the bench fodder as "
     "a species facing a hard winter."),
    ("Jose Mourinho",
     "Third person, wounded superiority, 'if I speak I am in big trouble'. "
     "Every setback is a conspiracy, every success is proof."),
    ("Sir Alex Ferguson",
     "Hairdryer restraint. Squeaky bum time, knowing asides about bottle "
     "and character, a grudge held in perfect working order."),
    ("Alan Sugar",
     "Boardroom brusqueness. Dismissals delivered as verdicts, 'with all "
     "due respect' meaning the opposite, pointing-finger energy. Sits in "
     "judgement on the squad and fires the bench fodder."),
    ("Jeremy Clarkson",
     "Hyperbolic superlatives, absurd comparisons, the cheapest bench "
     "player described as a national disgrace. Builds to 'and yet'."),
    ("Arnold Schwarzenegger",
     "Clipped imperatives, bodybuilding framing, 'come on', total "
     "conviction. No hedging anywhere."),
    ("Chris Kamara",
     "Delighted football chaos. 'Unbelievable, Jeff!' Missing the obvious "
     "then over-celebrating the trivial. Relentlessly cheerful."),
    ("Peter Kay",
     "Northern warmth. Domestic detail, a whole routine built out of "
     "something trivial like the bench order, circling back to the same "
     "joke until it is funnier."),
]


def persona_for(gameweek):
    """Returns the (name, voice note) whose turn it is.

    A fixed rotation rather than a random draw, so the schedule can be
    stated in advance and a re-run of an old gameweek reproduces it.
    """
    return GUEST_MANAGERS[(gameweek - 1) % len(GUEST_MANAGERS)]


@dataclass
class Player:
    name: str
    team: str
    pos: str                      # GKP / DEF / MID / FWD
    price: float                  # in millions
    proj: float = 0.0             # projected points
    opponent: str = ""            # e.g. "COV (H)"
    difficulty: int = 0           # 2-5, FPL fixture difficulty
    note: str = ""                # injury flag, role note
    is_captain: bool = False
    is_vice: bool = False


@dataclass
class Transfer:
    out: Player
    into: Player
    reason: str = ""


@dataclass
class Chip:
    name: str
    half: str                     # "GW1-19" / "GW20-38"
    status: str                   # "AVAILABLE" / "USED" / "LOCKED"
    detail: str = ""


@dataclass
class Section:
    """A block of reasoning prose, rendered under a heading."""
    title: str
    body: str                     # plain text; blank lines split paragraphs
    tone: str = "default"         # "default" | "warn"


@dataclass
class Report:
    gameweek: int
    entry_name: str = ""
    entry_id: int = 0
    deadline: str = ""            # human-readable
    squad_value: float = 0.0
    bank: float = 0.0
    transfer_status: str = ""     # e.g. "unlimited", "1 free"
    xi: list = field(default_factory=list)         # 11 Players, any order
    bench: list = field(default_factory=list)      # ordered subs
    transfers: list = field(default_factory=list)  # Transfer
    chips: list = field(default_factory=list)      # Chip
    chip_decision: str = ""
    captain_note: str = ""
    sections: list = field(default_factory=list)   # Section
    caveats: list = field(default_factory=list)    # strings
    projected_points: float = 0.0
    generated: str = ""
    persona_body: str = ""       # guest-pundit overview, in character
    persona_hint: str = ""       # playful label for the mystery pundit


# ------------------------------------------------------------------ helpers

def e(text):
    return _html.escape(str(text), quote=True)


def _shirt(player, size="normal"):
    fill, detail = TEAM_COLOURS.get(player.team, DEFAULT_COLOURS)
    badge = ""
    if player.is_captain:
        badge = '<span class="armband" title="Captain">C</span>'
    elif player.is_vice:
        badge = '<span class="armband vice" title="Vice-captain">V</span>'

    diff = f'<i class="d d{player.difficulty}"></i>' if player.difficulty else ""
    note = f'<em class="flag">{e(player.note)}</em>' if player.note else ""

    return f"""
      <figure class="shirt-card {size}">
        <div class="shirt" style="--kit:{fill};--trim:{detail}">
          <svg viewBox="0 0 64 60" aria-hidden="true">
            <path d="M20 4 L8 12 L2 26 L12 32 L14 56 L50 56 L52 32 L62 26 L56 12 L44 4
                     C40 10 24 10 20 4 Z"/>
          </svg>
          {badge}
        </div>
        <figcaption>
          <b>{e(player.name)}</b>
          <span class="meta">{e(player.team)} · £{player.price:.1f}m</span>
          <span class="opp">{diff}{e(player.opponent)}</span>
          {note}
        </figcaption>
      </figure>"""


def _pitch(xi):
    rows = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for p in xi:
        rows.setdefault(p.pos, []).append(p)
    for key in rows:
        rows[key].sort(key=lambda p: -p.proj)

    shape = "-".join(str(len(rows[k])) for k in ("DEF", "MID", "FWD")
                     if rows.get(k))
    lines = "".join(
        f'<div class="line line-{key.lower()}">'
        + "".join(_shirt(p) for p in rows[key]) + "</div>"
        for key in ("GKP", "DEF", "MID", "FWD") if rows.get(key)
    )
    return shape, f'<div class="pitch"><div class="turf"></div>{lines}</div>'


def _bench(bench):
    """Renders the bench.

    The reserve goalkeeper occupies squad position 12 and can only replace
    the starting keeper, so it is shown as its own slot rather than given a
    number in the outfield substitution order (positions 13-15).
    """
    if not bench:
        return ""

    keeper = next((p for p in bench if p.pos == "GKP"), None)
    outfield = [p for p in bench if p is not keeper]

    items = ""
    if keeper:
        items += (f'<li class="gk-slot"><span class="ord gk">GK</span>'
                  f'{_shirt(keeper, size="small")}</li>')
    items += "".join(
        f'<li><span class="ord">{i}</span>{_shirt(p, size="small")}</li>'
        for i, p in enumerate(outfield, start=1))

    return f"""
    <section class="bench">
      <h3>Bench <span class="sub">reserve keeper, then outfield
        substitution order</span></h3>
      <ol class="bench-list">{items}</ol>
    </section>"""


def _transfers(transfers):
    if not transfers:
        return ""
    rows = "".join(f"""
      <tr>
        <td class="out"><b>{e(t.out.name)}</b><span>{e(t.out.team)} ·
            £{t.out.price:.1f}m</span></td>
        <td class="arrow" aria-hidden="true">&rarr;</td>
        <td class="in"><b>{e(t.into.name)}</b><span>{e(t.into.team)} ·
            £{t.into.price:.1f}m</span></td>
        <td class="why">{e(t.reason)}</td>
      </tr>""" for t in transfers)

    return f"""
    <section id="transfers">
      <h2><span class="n">02</span> Transfers</h2>
      <table class="transfers">
        <thead><tr><th>Out</th><th></th><th>In</th><th>Rationale</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""


def _chips(chips, decision):
    if not chips:
        return ""
    halves = {}
    for c in chips:
        halves.setdefault(c.half, []).append(c)

    blocks = ""
    for half, items in halves.items():
        cells = "".join(f"""
          <div class="chip {c.status.lower()}">
            <b>{e(c.name)}</b>
            <span class="status">{e(c.status)}</span>
            {f'<em>{e(c.detail)}</em>' if c.detail else ''}
          </div>""" for c in items)
        blocks += f'<div class="chip-half"><h3>{e(half)}</h3>' \
                  f'<div class="chip-grid">{cells}</div></div>'

    note = f'<p class="decision">{e(decision)}</p>' if decision else ""
    return f"""
    <section id="chips">
      <h2><span class="n">03</span> Chips</h2>
      {note}
      <div class="chip-halves">{blocks}</div>
    </section>"""


def _sections(sections):
    out = ""
    for s in sections:
        paras = "".join(f"<p>{e(p.strip())}</p>"
                        for p in s.body.split("\n\n") if p.strip())
        out += f'<div class="note {s.tone}"><h3>{e(s.title)}</h3>{paras}</div>'
    return out


def _caveats(caveats):
    if not caveats:
        return ""
    items = "".join(f"<li>{e(c)}</li>" for c in caveats)
    return f"""
    <section id="caveats">
      <h2><span class="n">05</span> What this model cannot see</h2>
      <ul class="caveats">{items}</ul>
    </section>"""


# --------------------------------------------------------------------- CSS

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ink:#080D14; --ink-2:#0D141E; --panel:#111A26; --line:#1E2A3A;
  --chalk:#E8EDF2; --mute:#7D8FA3; --lime:#C6F24E; --amber:#FFAE3B;
  --red:#FF5C6E; --grass:#12301F; --grass-2:#164026;
  --display:"Avenir Next Condensed","HelveticaNeue-CondensedBold",
            "Arial Narrow",sans-serif;
  --body:"Iowan Old Style","Charter","Palatino Linotype",Georgia,serif;
  --mono:ui-monospace,"SF Mono",Menlo,monospace;
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--ink);color:var(--chalk);font-family:var(--body);
  line-height:1.6;font-size:16px;
  background-image:
    radial-gradient(ellipse 80% 50% at 50% -10%,#16283A 0%,transparent 60%),
    repeating-linear-gradient(90deg,transparent 0 79px,#ffffff05 79px 80px);
}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px 96px}

/* masthead */
header.mast{padding:72px 0 40px;border-bottom:2px solid var(--line);
  position:relative}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.32em;
  text-transform:uppercase;color:var(--lime);margin:0 0 18px}
.mast h1{font-family:var(--display);font-weight:700;letter-spacing:-.02em;
  font-size:clamp(44px,9vw,104px);line-height:.88;margin:0;
  text-transform:uppercase}
.mast h1 .gw{color:var(--lime)}
.mast .sub{font-size:17px;color:var(--mute);margin:18px 0 0;max-width:52ch}
.mast .big-n{position:absolute;top:40px;right:-8px;font-family:var(--display);
  font-size:clamp(120px,22vw,260px);line-height:.8;color:#ffffff07;
  font-weight:700;pointer-events:none;user-select:none}

/* stat strip */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line);margin:0 0 8px}
.stats div{background:var(--ink-2);padding:20px 18px}
.stats dt{font-family:var(--mono);font-size:10px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--mute);margin:0 0 8px}
.stats dd{margin:0;font-family:var(--display);font-size:32px;
  letter-spacing:-.01em}
.stats dd.lime{color:var(--lime)}

section{margin:64px 0 0}
h2{font-family:var(--display);text-transform:uppercase;letter-spacing:.01em;
  font-size:clamp(26px,4vw,40px);margin:0 0 24px;display:flex;
  align-items:baseline;gap:16px;border-bottom:1px solid var(--line);
  padding-bottom:14px}
h2 .n{font-family:var(--mono);font-size:12px;color:var(--lime);
  letter-spacing:.2em}
h3{font-family:var(--display);text-transform:uppercase;letter-spacing:.06em;
  font-size:18px;margin:0 0 14px;color:var(--chalk)}
h3 .sub{font-family:var(--body);text-transform:none;letter-spacing:0;
  color:var(--mute);font-size:13px;font-weight:400}

/* pitch */
.formation{font-family:var(--mono);color:var(--lime);font-size:13px;
  letter-spacing:.2em;margin:0 0 18px}
.pitch{position:relative;border:1px solid var(--line);border-radius:4px;
  padding:32px 16px;display:flex;flex-direction:column;gap:26px;
  overflow:hidden}
.turf{position:absolute;inset:0;z-index:0;
  background:
    linear-gradient(180deg,var(--grass-2),var(--grass)),
    repeating-linear-gradient(180deg,#ffffff08 0 56px,transparent 56px 112px);}
.turf::before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;
  background:#ffffff22}
.turf::after{content:"";position:absolute;left:50%;top:50%;width:132px;
  height:132px;border:1px solid #ffffff22;border-radius:50%;
  transform:translate(-50%,-50%)}
.line{position:relative;z-index:1;display:flex;justify-content:center;
  gap:clamp(6px,2.4vw,30px);flex-wrap:wrap}

.shirt-card{margin:0;width:92px;text-align:center;
  animation:rise .5s cubic-bezier(.2,.7,.3,1) backwards}
.line-gkp .shirt-card{animation-delay:.05s}
.line-def .shirt-card{animation-delay:.12s}
.line-mid .shirt-card{animation-delay:.19s}
.line-fwd .shirt-card{animation-delay:.26s}
@keyframes rise{from{opacity:0;transform:translateY(14px)}}
.shirt{position:relative;width:52px;margin:0 auto 8px;filter:
  drop-shadow(0 6px 10px #0006)}
.shirt svg{width:100%;display:block;fill:var(--kit);
  stroke:var(--trim);stroke-width:2.5}
.shirt-card.small{width:74px}
.shirt-card.small .shirt{width:38px}
.armband{position:absolute;right:-8px;top:-6px;width:24px;height:24px;
  border-radius:50%;background:var(--amber);color:#1A1200;font-family:var(--mono);
  font-size:12px;font-weight:700;display:grid;place-items:center;
  border:2px solid var(--ink)}
.armband.vice{background:var(--chalk);color:#10151C}
figcaption b{display:block;font-family:var(--display);font-size:15px;
  letter-spacing:.02em;line-height:1.15}
figcaption .meta{display:block;font-family:var(--mono);font-size:10px;
  color:#B9C6D4;margin-top:3px}
figcaption .opp{display:block;font-family:var(--mono);font-size:10px;
  color:#8FA0B2;margin-top:2px}
figcaption .flag{display:block;font-size:11px;color:var(--amber);
  font-style:normal;margin-top:3px}
.d{display:inline-block;width:7px;height:7px;border-radius:2px;
  margin-right:5px;vertical-align:middle}
.d2{background:#3FCF6A}.d3{background:#C9C13F}.d4{background:#E08A3C}
.d5{background:var(--red)}

/* bench */
.bench{margin-top:34px}
.bench-list{list-style:none;margin:0;padding:0;display:flex;
  gap:clamp(8px,2vw,24px);flex-wrap:wrap}
.bench-list li{display:flex;align-items:flex-start;gap:8px;
  background:var(--panel);border:1px solid var(--line);border-radius:4px;
  padding:14px 16px 12px}
.ord{font-family:var(--mono);font-size:11px;color:var(--lime);
  border:1px solid var(--line);border-radius:3px;padding:2px 6px}
.ord.gk{color:var(--amber);border-color:#3A2E18}
.bench-list .gk-slot{border-color:#3A2E18;margin-right:6px}

/* transfers */
table{width:100%;border-collapse:collapse;font-size:15px}
.transfers th{font-family:var(--mono);font-size:10px;letter-spacing:.2em;
  text-transform:uppercase;color:var(--mute);text-align:left;
  padding:0 14px 12px;font-weight:400}
.transfers td{padding:14px;border-top:1px solid var(--line);
  vertical-align:middle}
.transfers b{font-family:var(--display);font-size:17px;letter-spacing:.02em;
  display:block}
.transfers span{font-family:var(--mono);font-size:10px;color:var(--mute)}
.transfers .out b{color:var(--red)}
.transfers .in b{color:var(--lime)}
.transfers .arrow{color:var(--mute);font-size:20px;width:34px;
  text-align:center}
.transfers .why{color:var(--mute);font-size:14px;max-width:34ch}

/* chips */
.decision{font-size:18px;border-left:3px solid var(--lime);padding-left:18px;
  margin:0 0 28px;max-width:70ch}
.chip-halves{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:28px}
.chip-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:4px;
  padding:14px 16px}
.chip b{font-family:var(--display);font-size:15px;letter-spacing:.04em;
  text-transform:uppercase;display:block}
.chip .status{font-family:var(--mono);font-size:10px;letter-spacing:.16em;
  display:block;margin-top:6px}
.chip.available{border-left:3px solid var(--lime)}
.chip.available .status{color:var(--lime)}
.chip.used{opacity:.5;border-left:3px solid var(--mute)}
.chip.used .status{color:var(--mute)}
.chip.locked{border-left:3px solid var(--amber)}
.chip.locked .status{color:var(--amber)}
.chip em{display:block;font-style:normal;font-size:11px;color:var(--mute);
  margin-top:6px;line-height:1.4}

/* captain + notes */
.captains{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:20px;margin-bottom:26px}
.cap-card{background:var(--panel);border:1px solid var(--line);
  border-top:3px solid var(--amber);border-radius:4px;padding:22px 24px}
.cap-card.vice{border-top-color:var(--chalk)}
.cap-card .role{font-family:var(--mono);font-size:10px;letter-spacing:.24em;
  text-transform:uppercase;color:var(--amber)}
.cap-card.vice .role{color:var(--mute)}
.cap-card .who{font-family:var(--display);font-size:34px;line-height:1.05;
  margin:10px 0 4px;text-transform:uppercase}
.cap-card .fx{font-family:var(--mono);font-size:11px;color:var(--mute)}

.note{background:var(--ink-2);border:1px solid var(--line);border-radius:4px;
  padding:22px 26px;margin:0 0 16px}
.note p{margin:0 0 12px;color:#C3CEDA}.note p:last-child{margin:0}
.note.warn{border-left:3px solid var(--amber)}

.caveats{margin:0;padding:0 0 0 22px;color:#C3CEDA;max-width:76ch}
.caveats li{margin:0 0 12px}

footer{margin-top:72px;padding-top:22px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:11px;color:var(--mute);
  display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}

@media (max-width:640px){
  .wrap{padding:0 16px 64px}
  .shirt-card{width:72px}.shirt{width:42px}
  .transfers .why{display:none}
  .chip-grid{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}

/* tabs (CSS only - no script) */
.tabsw input{position:absolute;opacity:0;pointer-events:none}
nav.tabs{display:flex;gap:6px;margin:26px 0 0;border-bottom:2px solid var(--line)}
nav.tabs label{font-family:var(--display);text-transform:uppercase;
  letter-spacing:.08em;font-size:14px;padding:11px 20px;cursor:pointer;
  color:var(--mute);border:1px solid transparent;border-bottom:none;
  border-radius:4px 4px 0 0;user-select:none}
nav.tabs label:hover{color:var(--chalk)}
.panel{display:none}
#t-report:checked~nav.tabs label[for=t-report],
#t-sheet:checked~nav.tabs label[for=t-sheet]{
  color:var(--ink);background:var(--lime);border-color:var(--lime)}
#t-report:checked~.p-report{display:block}
#t-sheet:checked~.p-sheet{display:block}

/* compressed team sheet - built to fit one screenshot */
.sheet{display:grid;grid-template-columns:minmax(0,1.02fr) minmax(0,1fr);
  gap:20px;margin:20px 0 0;align-items:start}
.sheet h3{margin:0 0 10px;font-size:15px;letter-spacing:.12em}
.sheet .card{background:var(--ink-2);border:1px solid var(--line);
  border-radius:4px;padding:16px 18px}
.sheet .mini .pitch{padding:14px 6px;gap:11px}
.sheet .mini .shirt-card{width:62px}
.sheet .mini .shirt{width:34px;margin-bottom:5px}
.sheet .mini figcaption b{font-size:12px}
.sheet .mini figcaption .meta,.sheet .mini figcaption .opp{font-size:9px}
.sheet .mini .armband{width:18px;height:18px;font-size:9px;right:-6px;top:-4px}
.sheet .mini .bench{margin-top:14px}
.sheet .mini .bench-list li{padding:8px 10px 6px}
.sheet .mini .bench-list{gap:8px}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);
  border:1px solid var(--line);margin:0 0 12px}
.facts div{background:var(--ink-2);padding:8px 12px}
.facts dt{font-family:var(--mono);font-size:9px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--mute);margin:0 0 4px}
.facts dd{margin:0;font-family:var(--display);font-size:19px;line-height:1.1}
.facts dd.lime{color:var(--lime)}
.verdict{background:var(--ink-2);border:1px solid var(--line);
  border-left:3px solid var(--amber);border-radius:4px;padding:14px 16px}
.verdict .who{font-family:var(--mono);font-size:20px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--amber);margin:0 0 12px;line-height:1.25}
.verdict p{margin:0 0 8px;font-size:13.2px;line-height:1.5;color:#D2DCE6}
.verdict p:last-of-type{margin:0}
@media (max-width:900px){.sheet{grid-template-columns:1fr}}
"""


# ------------------------------------------------------------------ render

def render(report):
    """Builds the full HTML page for a :class:`Report`."""
    shape, pitch = _pitch(report.xi)
    captain = next((p for p in report.xi if p.is_captain), None)
    vice = next((p for p in report.xi if p.is_vice), None)

    generated = report.generated or datetime.now(timezone.utc).strftime(
        "%d %b %Y, %H:%M UTC")

    caps = ""
    if captain or vice:
        cards = ""
        for player, role, cls in ((captain, "Captain", ""),
                                  (vice, "Vice-captain", " vice")):
            if not player:
                continue
            cards += f"""
              <div class="cap-card{cls}">
                <div class="role">{role}</div>
                <div class="who">{e(player.name)}</div>
                <div class="fx">{e(player.team)} · {e(player.opponent)} ·
                  proj {player.proj:.1f} pts</div>
              </div>"""
        note = (f'<div class="note">{"".join(f"<p>{e(p.strip())}</p>" for p in report.captain_note.split(chr(10) + chr(10)) if p.strip())}</div>'
                if report.captain_note else "")
        caps = f"""
        <section id="captain">
          <h2><span class="n">04</span> Captaincy</h2>
          <div class="captains">{cards}</div>
          {note}
        </section>"""

    stats = f"""
      <dl class="stats">
        <div><dt>Squad value</dt><dd>£{report.squad_value:.1f}m</dd></div>
        <div><dt>In the bank</dt><dd>£{report.bank:.1f}m</dd></div>
        <div><dt>Transfers</dt><dd class="lime">{e(report.transfer_status)}</dd></div>
        <div><dt>Projected XI</dt><dd>{report.projected_points:.0f}<span
          style="font-size:15px;color:var(--mute)"> pts</span></dd></div>
      </dl>"""

    sheet = _team_sheet(report, pitch, shape)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GW{report.gameweek} Report · {e(report.entry_name)}</title>
<meta name="description" content="Fantasy Premier League gameweek
 {report.gameweek} team report: transfers, chip decision, starting XI and
 captaincy.">
<style>{CSS}</style>
</head><body>
<div class="wrap">

<header class="mast">
  <div class="big-n" aria-hidden="true">{report.gameweek:02d}</div>
  <p class="kicker">Gameweek {report.gameweek} · Team Report</p>
  <h1>{e(report.entry_name)}<br><span class="gw">GW{report.gameweek}</span></h1>
  <p class="sub">Deadline {e(report.deadline)}. Entry {report.entry_id}.
     Decisions and the reasoning behind them.</p>
</header>

<div class="tabsw">
  <input type="radio" name="tabs" id="t-report" checked>
  <input type="radio" name="tabs" id="t-sheet">
  <nav class="tabs">
    <label for="t-report" data-tab="report">Full report</label>
    <label for="t-sheet" data-tab="sheet">Team sheet</label>
  </nav>

  <div class="panel p-report">
    {stats}

    <section id="xi">
      <h2><span class="n">01</span> Starting XI</h2>
      <p class="formation">FORMATION {e(shape)}</p>
      {pitch}
      {_bench(report.bench)}
    </section>

    {_transfers(report.transfers)}
    {_chips(report.chips, report.chip_decision)}
    {caps}

    {f'<section id="reasoning"><h2><span class="n">06</span> Decision process</h2>{_sections(report.sections)}</section>' if report.sections else ''}

    {_caveats(report.caveats)}
  </div>

  <div class="panel p-sheet">{sheet}</div>
</div>

<script>
(function(){{
  var map={{sheet:'t-sheet',report:'t-report'}};
  function apply(){{
    var id=map[(location.hash||'').replace('#','')];
    if(id){{var el=document.getElementById(id); if(el) el.checked=true;}}
  }}
  apply();
  window.addEventListener('hashchange',apply);
  document.querySelectorAll('nav.tabs label').forEach(function(l){{
    l.addEventListener('click',function(){{
      history.replaceState(null,'','#'+l.dataset.tab);
    }});
  }});
}})();
</script>

<footer>
  <span>Generated {e(generated)}</span>
  <span>Fantasy Premier League · entry {report.entry_id}</span>
</footer>
</div>
</body></html>"""


def _team_sheet(report, pitch, shape):
    """The compressed one-screen view: team on the left, verdict on the right."""
    captain = next((p for p in report.xi if p.is_captain), None)
    vice = next((p for p in report.xi if p.is_vice), None)

    played = [c.name for c in report.chips if c.status.upper() == "USED"]
    chip_line = ", ".join(played) if played else "None"

    facts = f"""
      <dl class="facts">
        <div><dt>Captain</dt><dd class="lime">{e(captain.name) if captain else '-'}</dd></div>
        <div><dt>Vice</dt><dd>{e(vice.name) if vice else '-'}</dd></div>
        <div><dt>Chip played</dt><dd>{e(chip_line)}</dd></div>
        <div><dt>Transfers</dt><dd>{len(report.transfers)} · {e(report.transfer_status)}</dd></div>
        <div><dt>Squad value</dt><dd>£{report.squad_value:.1f}m</dd></div>
        <div><dt>Projected XI</dt><dd>{report.projected_points:.0f} pts</dd></div>
      </dl>"""

    verdict = ""
    if report.persona_body:
        paras = "".join(f"<p>{e(p.strip())}</p>"
                        for p in report.persona_body.split("\n\n") if p.strip())
        hint = report.persona_hint or "This week's guest pundit"
        verdict = f"""
        <div class="verdict">
          <p class="who">{e(hint)}</p>
          {paras}
        </div>"""

    return f"""
      <div class="sheet">
        <div>
          <h3>The team · {e(shape)}</h3>
          <div class="mini">{pitch}{_bench(report.bench)}</div>
        </div>
        <div>
          <h3>The verdict</h3>
          {facts}
          {verdict}
        </div>
      </div>"""
