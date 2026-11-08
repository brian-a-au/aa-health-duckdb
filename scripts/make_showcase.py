"""Make the README slideshow (docs/showcase.gif and docs/showcase.png).

Every slide is a part of the real report, rendered from synthetic data made in this
script. No Adobe API, credential or customer data is used. Needs Google Chrome and
FFmpeg on this computer.

Run: uv run python scripts/make_showcase.py --out docs
"""

from __future__ import annotations

import argparse
import math
import random
import shutil
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.render.html import render_html
from ahd.store import Store
from ahd.summary import build_view

TODAY = date(2026, 11, 7)
YESTERDAY = TODAY - timedelta(days=1)
DAYS = [TODAY - timedelta(days=91 - i) for i in range(92)]
FPS = 8
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
]

EVENTS = [
    "Product Views",
    "Cart Adds",
    "Cart Removals",
    "Checkouts",
    "Orders",
    "Revenue",
    "Sign-ups",
    "Logins",
    "Searches",
    "Store Locator Uses",
    "Video Starts",
    "Video Completes",
    "Newsletter Sign-ups",
    "Wishlist Adds",
    "Coupon Uses",
    "Reviews Written",
    "Chat Starts",
    "App Crash Reports",
    "Form Starts",
    "Form Completes",
    "Downloads",
    "Legacy Promo Clicks",
    "Share Clicks",
    "Return Requests",
]
EVARS = [
    "Campaign Code",
    "Internal Search Term",
    "Product Finding Method",
    "Login Status",
    "Customer Type",
    "Page Type",
    "Form Name",
    "Video Name",
    "Store ID",
    "Promo Code",
    "Language",
    "Experiment Variant",
    "Content Category",
    "App Version",
    "Membership Tier",
    "Referral Partner",
]
PROPS = [
    "Site Section",
    "Page Template",
    "Search Term",
    "Error Message",
    "Content Author",
    "Breadcrumb",
    "Device Orientation",
    "Previous Page",
]
PAGES = [
    "home",
    "search results",
    "category: women",
    "category: men",
    "product details",
    "cart",
    "checkout: shipping",
    "checkout: payment",
    "order confirmation",
    "sign in",
    "account: orders",
    "store locator",
    "help: returns",
    "blog: style guide",
    "category: kids",
    "category: sale",
    "wishlist",
    "account: profile",
    "help: shipping",
    "careers",
]

# What happens yesterday, to give the report something to show.
YESTERDAY_FACTOR = {
    "metrics/event2": 1.95,  # Cart Adds spike
    "metrics/event5": 0.42,  # Orders drop
    "metrics/evar1instances": 0.35,  # Campaign Code drop
    "variables/prop3": 1.8,  # Search Term spike
    "metrics/event18": 0.0,  # App Crash Reports stopped
}
ALWAYS_ZERO = {"metrics/event22", "metrics/evar16instances"}


def components() -> list[Component]:
    out = []
    for i, name in enumerate(EVENTS, 1):
        cid = f"metrics/event{i}"
        out.append(Component(cid, name, "metric", "custom_events", i, Column(cid, cid), True))
    for i, name in enumerate(EVARS, 1):
        cid = f"metrics/evar{i}instances"
        out.append(Component(cid, name, "dimension", "custom_evars", i, Column(cid, cid), True))
    for i, name in enumerate(PROPS, 1):
        cid = f"variables/prop{i}"
        out.append(
            Component(
                cid,
                name,
                "dimension",
                "custom_props",
                i,
                Column(cid, "metrics/occurrences", cid),
                True,
            )
        )
    out.append(
        Component(
            "variables/page",
            "Page",
            "dimension",
            "Reserved Props",
            None,
            Column("variables/page", "metrics/occurrences", "variables/page"),
            True,
        )
    )
    return out


def series(rng: random.Random, cid: str, base: float) -> list[DailyValue]:
    out = []
    for i, day in enumerate(DAYS):
        weekly = 1.0 + 0.18 * math.sin(2 * math.pi * (day.weekday() + 1) / 7)
        trend = 1.0 + 0.10 * i / len(DAYS)
        expected = base * weekly * trend
        if cid in ALWAYS_ZERO:
            out.append(DailyValue(cid, day, 0.0, 0.0, 0.0, 0.0, False))
            continue
        value = expected * rng.uniform(0.86, 1.14)
        if day == YESTERDAY and cid in YESTERDAY_FACTOR:
            value = expected * YESTERDAY_FACTOR[cid]
        if i in (44, 45) and cid in ("metrics/event1", "variables/page"):
            value = expected * 0.3  # an old tracking outage, flagged in the chart
        upper, lower = expected * 1.28, expected * 0.74
        value = round(value)
        out.append(
            DailyValue(
                cid, day, float(value), expected, upper, lower, value > upper or value < lower
            )
        )
    return out


def build_store(path: Path) -> tuple[Store, int]:
    rng = random.Random(7)
    store = Store.open(path)
    rid = store.create_run(
        "example.prod",
        "Example Retail",
        None,
        TODAY,
        "US/Pacific",
        "0.1.0",
        dimension="variables/page",
        metric="metrics/occurrences",
    )
    comps = components()
    store.add_components(rid, comps)
    values: list[DailyValue] = []
    for c in comps:
        base = rng.choice([800, 1500, 3200, 6400, 12000, 24000, 52000])
        values += series(rng, c.id, base)
    values += [
        DailyValue("individual", v.day, v.value, v.expected, v.upper, v.lower, v.anomaly)
        for v in series(rng, "variables/page", 310000)
    ]
    store.add_daily_values(rid, values)
    current = [(i + 1, p, float(round(32000 * 0.82**i))) for i, p in enumerate(PAGES)]
    hist_order = (
        PAGES[:3] + [PAGES[4], PAGES[3]] + PAGES[5:12] + [PAGES[13], PAGES[12]] + PAGES[14:19]
    )
    historical = [(i + 1, p, float(round(2_900_000 * 0.82**i))) for i, p in enumerate(hist_order)]
    store.add_top_items(rid, "current", current)
    store.add_top_items(rid, "historical", historical)
    for kind, labels, shares in (
        (
            "browser",
            ["Google", "Microsoft", "Apple", "Mozilla", "Other"],
            [0.5, 0.15, 0.22, 0.04, 0.09],
        ),
        (
            "device",
            ["Other", "Mobile Phone", "Tablet", "Media Player", "Television"],
            [0.62, 0.27, 0.07, 0.0, 0.04],
        ),
    ):
        rows = []
        for i, page in enumerate(PAGES[:10]):
            total = 32000 * 0.82**i
            rows.append((page, [float(round(total * s * rng.uniform(0.7, 1.3))) for s in shares]))
        totals = [float(round(sum(r[1][j] for r in rows) * 1.6)) for j in range(len(labels))]
        store.add_breakdown(rid, kind, labels, rows, totals)
    store.finish_run(rid, "ok")
    return store, rid


# Each slide: step label, headline, description, proof lines, setup script, parts to keep.
SCENES = [
    (
        "Glance",
        "Yesterday,<br>at a glance.",
        "See how many components were checked, how many Adobe flagged, and which ones had no data.",
        "Summary tiles<br>Status with an icon and a label",
        "",
        [".page-head", "#tiles"],
        "light",
    ),
    (
        "Trend",
        "Spot the<br>outliers.",
        "Each chart shows 90 days of values against the range Adobe expected. Flagged days stand out.",
        "Adobe anomaly detection<br>Expected range and flags",
        (
            "var s=document.getElementById('metric-pick');s.value='metrics/event5';"
            "s.dispatchEvent(new Event('change'));"
        ),
        ["#panel-components .charts"],
        "light",
    ),
    (
        "Components",
        "Every event,<br>eVar and prop.",
        "Compare each component with its usual value for that weekday. Show only the flagged ones.",
        "Daily or weekly view<br>Sortable tables",
        "document.getElementById('flagged-only').click();",
        ["#panel-components .controls", "#tables"],
        "light",
    ),
    (
        "Alerts",
        "Know what<br>to check.",
        "Unusual values come first, with how far each one is from the expected value. Zero data comes next.",
        "Copy the alert summary<br>Paste it into an email or chat",
        "document.getElementById('tab-alerts').click();",
        ["#panel-alerts"],
        "light",
    ),
    (
        "Detail",
        "A closer<br>look.",
        "See the top values of one dimension for each browser group and device type.",
        "Page by Occurrences<br>Browser and device breakdowns",
        "document.getElementById('tab-individual').click();",
        [
            "#panel-individual h2:nth-of-type(3)",
            "#panel-individual .hint:nth-of-type(2)",
            "#browser",
        ],
        "light",
    ),
    (
        "Offline",
        "One file.<br>Light or dark.",
        "Open the report in any browser. It loads nothing from the internet, and the data stays on your computer.",
        "Local DuckDB file + one HTML file<br>No server · No network requests",
        "",
        [".page-head", "#tiles", "#status"],
        "dark",
    ),
]
SECONDS = 4.5

# Keep only the chosen parts of the page, in order, at the top of the body.
KEEP_JS = """
(function(sel){
  var keep = sel.map(function(s){ var n = document.querySelector(s);
    if (!n) throw new Error('missing ' + s);
    var c = n.cloneNode(true);
    // A clone does not keep what a select shows, so copy each chosen option across.
    var from = n.querySelectorAll('select'), to = c.querySelectorAll('select');
    for (var i = 0; i < from.length; i++) to[i].selectedIndex = from[i].selectedIndex;
    return c; });
  var body = document.body;
  while (body.firstChild) body.removeChild(body.firstChild);
  keep.forEach(function(n){ n.hidden = false; body.appendChild(n); });
  body.style.paddingTop = '18px';
})(%s);
"""

FRAME = """<!doctype html><html lang="en"><meta charset="utf-8"><style>
:root { --ink:#15171a; --paper:#fcfcfb; --headline:#f0efec; --body:#c3c2b7; --muted:#9a998f;
  --faint:#6b6a64; --accent:#6da7ec; --line:#2c2c2a;
  --serif:"Charter","Iowan Old Style","Source Serif Pro",Georgia,serif;
  --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --mono:"SF Mono",Menlo,Consolas,monospace; }
* { box-sizing:border-box; } body { margin:0; background:var(--ink); }
.stage { position:relative; width:1120px; height:720px; overflow:hidden; background:var(--ink);
  color:var(--headline); font-family:var(--ui); }
header { position:absolute; inset:36px 40px auto; display:flex; align-items:center;
  justify-content:space-between; }
.brand { display:flex; align-items:center; gap:11px; font-size:15px; font-weight:600; }
.brand b { width:19px; height:19px; border-radius:5px; background:var(--accent); }
.tag { font-size:10.5px; font-weight:500; letter-spacing:.18em; color:var(--muted);
  text-transform:uppercase; }
.story { position:absolute; left:40px; top:150px; width:272px; }
.kicker { color:var(--accent); font-size:11px; font-weight:600; letter-spacing:.14em;
  text-transform:uppercase; }
h1 { font-family:var(--serif); font-size:44px; font-weight:500; line-height:1.06;
  letter-spacing:-.02em; margin:22px 0 20px; }
.description { font-size:16px; line-height:1.6; color:var(--body); margin:0; }
.proof { border-top:1px solid var(--line); margin-top:26px; padding-top:18px;
  font-family:var(--mono); font-size:12px; line-height:1.9; color:var(--muted); }
.window { position:absolute; top:96px; left:348px; width:732px; height:552px;
  background:var(--paper); border-radius:12px; overflow:hidden; border:1px solid rgba(0,0,0,.4);
  box-shadow:0 26px 50px -20px rgba(0,0,0,.6); }
.window.dark { background:#0d0d0d; }
.bar { height:38px; background:#efede4; display:flex; align-items:center; gap:7px;
  padding:0 16px; border-bottom:1px solid #dbd8cc; }
.window.dark .bar { background:#232321; border-bottom-color:#2c2c2a; }
.dot { width:9px; height:9px; border-radius:50%; }
.dot:nth-child(1){background:#c89184}.dot:nth-child(2){background:#cbae79}
.dot:nth-child(3){background:#8faa85}
.filename { margin-left:11px; color:#8a8a82; font:11px/1 var(--mono); }
.shot { display:block; width:732px; }
footer { position:absolute; bottom:27px; left:40px; right:40px; display:flex;
  align-items:center; justify-content:space-between; }
.steps { display:flex; gap:18px; font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); }
.steps span { display:inline-flex; gap:8px; } .steps b { font-family:var(--mono);
  font-weight:400; font-size:10px; color:var(--faint); }
.steps span.on { color:var(--headline); } .steps span.on b { color:var(--accent); }
.disclosure { font-size:10.5px; letter-spacing:.03em; color:var(--faint); }
</style><div class="stage">
<header><div class="brand"><b></b> aa-health-duckdb</div>
<div class="tag">Adobe Analytics · Report suite health</div></header>
<div class="story"><div class="kicker">{{kicker}}</div><h1>{{title}}</h1>
<p class="description">{{description}}</p><div class="proof">{{proof}}</div></div>
<div class="window {{theme}}"><div class="bar"><i class="dot"></i><i class="dot"></i>
<i class="dot"></i><span class="filename">ahd_example.prod_2026-11-07_run1.html · excerpt</span></div>
<img class="shot" src="{{shot}}" alt=""></div>
<footer><div class="steps">{{steps}}</div>
<div class="disclosure">Synthetic data · Actual report excerpts</div></footer>
</div></html>"""


def chrome() -> str:
    for p in CHROME_PATHS:
        if Path(p).exists() or shutil.which(p):
            return p
    raise RuntimeError("Google Chrome or Chromium is required")


def screenshot(
    browser: str, page: Path, out: Path, width: int, height: int, scale: int, dark: bool = False
) -> None:
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--allow-file-access-from-files",
        f"--force-device-scale-factor={scale}",
        f"--window-size={width},{height}",
        f"--screenshot={out}",
    ]
    cmd += (
        ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
        if dark
        else ["--blink-settings=preferredColorScheme=1"]
    )
    subprocess.run(cmd + [page.as_uri()], check=True, capture_output=True)


def make(out_dir: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required")
    browser = chrome()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ahd-showcase-") as tmp:
        work = Path(tmp)
        store, rid = build_store(work / "showcase.duckdb")
        html = render_html(build_view(store, rid))
        stills = []
        for i, (step, title, desc, proof, setup, keep, theme) in enumerate(SCENES):
            theme_js = f"document.querySelector('[data-theme-choice={theme}]').click();"
            script = setup + theme_js + KEEP_JS % repr(keep)
            page = work / f"excerpt{i}.html"
            page.write_text(
                html.replace("</body>", f"<script>{script}</script></body>"), encoding="utf-8"
            )
            # Excerpts render at 920 CSS pixels wide and are scaled to the 732 pixel window.
            shot = work / f"excerpt{i}.png"
            screenshot(browser, page, shot, 920, 650, 2, dark=theme == "dark")
            steps = "".join(
                f'<span class="{"on" if j == i else ""}"><b>{j + 1:02d}</b> {s[0]}</span>'
                for j, s in enumerate(SCENES)
            )
            frame = work / f"frame{i}.html"
            fields = {
                "kicker": f"{i + 1:02d} / {step}",
                "title": title,
                "description": desc,
                "proof": proof,
                "theme": theme,
                "shot": shot.name,
                "steps": steps,
            }
            text = FRAME
            for key, value in fields.items():
                text = text.replace("{{" + key + "}}", value)
            frame.write_text(text, encoding="utf-8")
            still = work / f"scene{i}.png"
            screenshot(browser, frame, still, 1120, 720, 1)
            stills.append(still)
        shutil.copyfile(stills[0], out_dir / "showcase.png")
        listing = work / "scenes.txt"
        listing.write_text(
            "".join(f"file '{s}'\nduration {SECONDS}\n" for s in stills) + f"file '{stills[-1]}'\n",
            encoding="utf-8",
        )
        total = SECONDS * len(stills)
        # A thin progress line along the bottom edge, moving left to right over the loop.
        graph = (
            f"[0:v]fps={FPS},format=rgb24[v];"
            f"color=c=0x6da7ec:s=1120x2:r={FPS}[bar];"
            f"[v][bar]overlay=x='-1120+1120*t/{total}':y=718:shortest=1[o];"
            "[o]split[a][b];[a]palettegen=stats_mode=full[p];"
            "[b][p]paletteuse=dither=none:diff_mode=rectangle"
        )
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-filter_complex",
                graph,
                "-t",
                str(total),
                "-loop",
                "0",
                str(out_dir / "showcase.gif"),
            ],
            check=True,
        )
        store.con.close()
    print(f"Wrote {out_dir / 'showcase.gif'} ({total:g}s) and {out_dir / 'showcase.png'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs"))
    make(parser.parse_args().out)


if __name__ == "__main__":
    main()
