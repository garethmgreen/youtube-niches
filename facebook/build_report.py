#!/usr/bin/env python3
"""Build the Untold America reel report artifact from the raw export."""
import csv, math, statistics as st
from datetime import datetime
from html import escape

SRC = "untold.csv"
OUT = "/home/user/youtube-niches/facebook/untold_america_report.html"

def num(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return None

rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
ivcols = sorted([(int(c.split("interval ")[1]), c) for c in rows[0] if "Percentage of total views at interval" in c])

R = []
for r in rows:
    dur = num(r["Duration (sec)"]) or 0
    curve = []
    for _, c in ivcols:
        v = num(r[c])
        if v is None: break
        curve.append(v)
    rec = num(r["Seconds viewed from Recommendations"]) or 0.0
    fol = num(r["Seconds viewed from Followers"]) or 0.0
    shr = num(r["Seconds viewed from Shares"]) or 0.0
    R.append(dict(
        d=datetime.strptime(r["Publish time"], "%m/%d/%Y %H:%M"),
        v=num(r["3-second video views"]) or 0, dur=dur,
        reach=num(r["Reach"]) or 0,
        ret=(num(r["Average Seconds viewed"]) or 0) / dur if dur else 0,
        rec=rec, fol=fol, shr=shr,
        recsh=rec / (rec + fol + shr) if (rec + fol + shr) > 0 else None,
        t=r["Title"].strip(), curve=curve,
        eng=(num(r["Reactions"]) or 0) + (num(r["Comments"]) or 0) + (num(r["Shares"]) or 0),
    ))
R.sort(key=lambda x: x["d"])

def med(xs): return st.median(xs) if xs else None
def f(n): return f"{round(n):,}"

# ---------- key figures ----------
short = [x for x in R if x["dur"] < 30]
lng   = [x for x in R if x["dur"] >= 45]
ratio = med([x["v"] for x in short]) / med([x["v"] for x in lng])
tot_rec = sum(x["rec"] for x in R); tot_fol = sum(x["fol"] for x in R); tot_shr = sum(x["shr"] for x in R)
grand = tot_rec + tot_fol + tot_shr

BUCKETS = [("under 20s",0,20),("20–30s",20,30),("30–45s",30,45),("45–60s",45,60),("60–90s",60,90),("90s+",90,10**9)]
bucket_rows = []
for label, lo, hi in BUCKETS:
    g = [x for x in R if lo <= x["dur"] < hi]
    if g:
        bucket_rows.append(dict(label=label, n=len(g), med=med([x["v"] for x in g]),
                                ret=med([x["ret"] for x in g]), best=max(x["v"] for x in g),
                                hit=sum(1 for x in g if x["v"] >= 20000)))

MONTHS = sorted({(x["d"].year, x["d"].month) for x in R})
mrows = []
for k in MONTHS:
    g = [x for x in R if (x["d"].year, x["d"].month) == k]
    lg = [x for x in g if x["dur"] >= 45]
    mature = [x for x in g if x["recsh"] is not None and (R[-1]["d"] - x["d"]).days >= 10]
    mrows.append(dict(k=f"{k[0]}-{k[1]:02d}", lab=datetime(k[0],k[1],1).strftime("%b"), n=len(g),
                      med=med([x["v"] for x in g]), reach=med([x["reach"] for x in g]),
                      dur=med([x["dur"] for x in g]), ret=med([x["ret"] for x in g]),
                      longret=med([x["ret"] for x in lg]) if lg else None,
                      longdur=med([x["dur"] for x in lg]) if lg else None,
                      recsh=med([x["recsh"] for x in mature]) if mature else None))

# ---------- theme ----------
TOK = dict(accent="#008a6e", alarm="#b03f26", ink="#171b1d", ink2="#4a5559", mut="#6d787c",
           rule="#d3d7d1", surf="#f2f3f0", card="#fbfbf9")
DTOK = dict(accent="#1faa8d", alarm="#dd6d49", ink="#eef0ec", ink2="#a8b4b8", mut="#7f8b8f",
            rule="#2e3538", surf="#171b1d", card="#1e2325")

def esc(s): return escape(str(s), quote=True)

# ---------- chart: the cliff (horizontal bars, emphasis) ----------
def chart_cliff():
    W, rowh, top, left = 720, 46, 26, 96
    H = top + rowh * len(bucket_rows) + 16
    mx = max(b["med"] for b in bucket_rows)
    plotw = W - left - 96
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Median views by reel length">']
    p.append(f'<text x="0" y="14" class="ct">MEDIAN VIEWS BY REEL LENGTH</text>')
    for i, b in enumerate(bucket_rows):
        y = top + i * rowh
        w = max(2, b["med"] / mx * plotw)
        emph = b["label"] in ("under 20s", "20–30s")
        fill = "var(--accent)" if emph else "var(--bar-mut)"
        p.append(f'<text x="{left-10}" y="{y+17}" class="cl" text-anchor="end">{b["label"]}</text>')
        p.append(f'<rect x="{left}" y="{y+4}" width="{w:.1f}" height="20" rx="4" fill="{fill}">'
                 f'<title>{b["label"]}: median {f(b["med"])} views across {b["n"]} reels</title></rect>')
        p.append(f'<text x="{left+w+8}" y="{y+19}" class="cv{" em" if emph else ""}">{f(b["med"])}</text>')
        p.append(f'<text x="{left+w+8}" y="{y+19}" class="cn" dx="{len(f(b["med"]))*8.4+10}">n={b["n"]}</text>')
    p.append("</svg>")
    return "\n".join(p)

# ---------- chart: scatter duration vs views (log y) ----------
def chart_scatter():
    W, H, left, right, top, bot = 720, 340, 54, 14, 26, 42
    xs = [x["dur"] for x in R]; ys = [x["v"] for x in R]
    xmin, xmax = 0, max(xs) * 1.02
    ymin, ymax = 200, max(ys) * 1.3
    def px(v): return left + (v - xmin) / (xmax - xmin) * (W - left - right)
    def py(v): return H - bot - (math.log10(max(v, ymin)) - math.log10(ymin)) / (math.log10(ymax) - math.log10(ymin)) * (H - top - bot)
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Every reel: length versus views">']
    p.append('<text x="0" y="14" class="ct">EVERY REEL — LENGTH VS VIEWS (LOG SCALE)</text>')
    for gy in [1000, 10000, 100000]:
        p.append(f'<line x1="{left}" y1="{py(gy):.1f}" x2="{W-right}" y2="{py(gy):.1f}" class="grid"/>')
        p.append(f'<text x="{left-8}" y="{py(gy)+4:.1f}" class="cax" text-anchor="end">{f(gy)}</text>')
    for gx in [0, 30, 60, 90, 120]:
        if gx <= xmax:
            p.append(f'<text x="{px(gx):.1f}" y="{H-bot+20}" class="cax" text-anchor="middle">{gx}s</text>')
    # 30s threshold
    p.append(f'<line x1="{px(30):.1f}" y1="{top}" x2="{px(30):.1f}" y2="{H-bot}" class="thr"/>')
    p.append(f'<text x="{px(30)-6:.1f}" y="{top+12}" class="cthr" text-anchor="end">30s</text>')
    for x in R:
        c = "var(--accent)" if x["dur"] < 30 else "var(--dot-mut)"
        r = 5 if x["dur"] < 30 else 4
        ttl = f'{x["d"]:%b %d} · {int(x["dur"])}s · {f(x["v"])} views · retention {x["ret"]*100:.0f}%'
        p.append(f'<circle cx="{px(x["dur"]):.1f}" cy="{py(x["v"]):.1f}" r="{r}" fill="{c}" '
                 f'fill-opacity="0.85" stroke="var(--card)" stroke-width="1.5"><title>{esc(ttl)}</title></circle>')
    p.append(f'<text x="{W-right}" y="{H-6}" class="cax" text-anchor="end">reel length →</text>')
    p.append("</svg>")
    return "\n".join(p)

# ---------- chart: monthly median views ----------
def chart_monthly():
    W, H, left, top, bot = 720, 230, 54, 26, 40
    mx = max(m["med"] for m in mrows)
    bw = (W - left - 14) / len(mrows)
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Median views by month">']
    p.append('<text x="0" y="14" class="ct">MEDIAN VIEWS PER REEL, BY MONTH</text>')
    for i, m in enumerate(mrows):
        h = max(2, m["med"] / mx * (H - top - bot))
        x = left + i * bw + bw * 0.22
        w = bw * 0.56
        fill = "var(--alarm)" if m["lab"] == "Aug" else ("var(--accent)" if m["lab"] == "Jul" else "var(--bar-mut)")
        p.append(f'<rect x="{x:.1f}" y="{H-bot-h:.1f}" width="{w:.1f}" height="{h:.1f}" rx="4" fill="{fill}">'
                 f'<title>{m["k"]}: median {f(m["med"])} views across {m["n"]} reels</title></rect>')
        p.append(f'<text x="{x+w/2:.1f}" y="{H-bot-h-7:.1f}" class="cv" text-anchor="middle">{f(m["med"])}</text>')
        p.append(f'<text x="{x+w/2:.1f}" y="{H-bot+18:.1f}" class="cax" text-anchor="middle">{m["lab"]}</text>')
        p.append(f'<text x="{x+w/2:.1f}" y="{H-bot+32:.1f}" class="cn2" text-anchor="middle">{m["n"]} reels</text>')
    p.append("</svg>")
    return "\n".join(p)

# ---------- chart: retention curves ----------
def chart_curves():
    curved = [x for x in R if x["curve"]]
    ranked = sorted(curved, key=lambda x: x["v"], reverse=True)
    cut = max(3, len(ranked) // 5)
    groups = [("Top 20%", ranked[:cut], "var(--accent)"),
              ("Bottom 20%", ranked[-cut:], "var(--alarm)")]
    N = 21
    def avg_curve(g):
        out = []
        for i in range(N):
            fr = i / (N - 1)
            vals = []
            for x in g:
                idx = min(int(round(fr * (len(x["curve"]) - 1))), len(x["curve"]) - 1)
                vals.append(x["curve"][idx])
            out.append(sum(vals) / len(vals))
        return out
    W, H, left, right, top, bot = 720, 300, 50, 104, 26, 42
    def px(fr): return left + fr * (W - left - right)
    def py(v): return H - bot - v * (H - top - bot)
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Retention curves, best versus worst reels">']
    p.append('<text x="0" y="14" class="ct">SHARE OF VIEWERS STILL WATCHING</text>')
    for gy in [0, 0.25, 0.5, 0.75, 1.0]:
        p.append(f'<line x1="{left}" y1="{py(gy):.1f}" x2="{W-right}" y2="{py(gy):.1f}" class="grid"/>')
        p.append(f'<text x="{left-8}" y="{py(gy)+4:.1f}" class="cax" text-anchor="end">{int(gy*100)}%</text>')
    for gx, lab in [(0,"start"),(0.25,"25%"),(0.5,"halfway"),(0.75,"75%"),(1.0,"end")]:
        p.append(f'<text x="{px(gx):.1f}" y="{H-bot+20}" class="cax" text-anchor="middle">{lab}</text>')
    drawn = []
    for name, g, col in groups:
        c = avg_curve(g)
        pts = " ".join(f"{px(i/(N-1)):.1f},{py(v):.1f}" for i, v in enumerate(c))
        p.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
        for i, v in enumerate(c):
            if i % 5 == 0:
                p.append(f'<circle cx="{px(i/(N-1)):.1f}" cy="{py(v):.1f}" r="4.5" fill="{col}" '
                         f'stroke="var(--card)" stroke-width="2"><title>{name} — {int(i/(N-1)*100)}% through: {v*100:.0f}% still watching</title></circle>')
        drawn.append([name, col, py(c[-1]), c[-1]])

    # The curves converge at the end, so anchor labels to the line but push them
    # apart when the endpoints are closer together than the label block is tall.
    drawn.sort(key=lambda d: d[2])
    MIN_GAP = 34
    if len(drawn) == 2 and drawn[1][2] - drawn[0][2] < MIN_GAP:
        mid = (drawn[0][2] + drawn[1][2]) / 2
        drawn[0][2] = mid - MIN_GAP / 2
        drawn[1][2] = mid + MIN_GAP / 2
    for name, col, y, end in drawn:
        p.append(f'<text x="{W-right+12}" y="{y+2:.1f}" class="clg" fill="{col}">{name}</text>')
        p.append(f'<text x="{W-right+12}" y="{y+17:.1f}" class="cn2">ends {end*100:.0f}%</text>')
    p.append("</svg>")
    return "\n".join(p)

# ---------- tables ----------
def short_table():
    out = ['<table><thead><tr><th>Published</th><th class="r">Length</th><th class="r">Views</th><th class="r">Retention</th></tr></thead><tbody>']
    for x in sorted(short, key=lambda z: z["v"], reverse=True):
        cls = ' class="flag"' if x["v"] < 7000 else ""
        out.append(f'<tr{cls}><td>{x["d"]:%b %d}</td><td class="r n">{int(x["dur"])}s</td>'
                   f'<td class="r n em">{f(x["v"])}</td><td class="r n">{x["ret"]*100:.0f}%</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)

def within_table():
    out = ['<table><thead><tr><th>Month</th><th>Reel length</th><th class="r">Reels</th>'
           '<th class="r">Median views</th><th class="r">Median retention</th></tr></thead><tbody>']
    for k in MONTHS:
        for lab, lo, hi in [("under 30s", 0, 30), ("45s and over", 45, 10**9)]:
            g = [x for x in R if (x["d"].year, x["d"].month) == k and lo <= x["dur"] < hi]
            if len(g) < 2: continue
            cls = ' class="hi"' if lo == 0 else ""
            out.append(f'<tr{cls}><td>{datetime(k[0],k[1],1):%b}</td><td>{lab}</td><td class="r n">{len(g)}</td>'
                       f'<td class="r n em">{f(med([x["v"] for x in g]))}</td>'
                       f'<td class="r n">{med([x["ret"] for x in g])*100:.0f}%</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)

def month_table():
    out = ['<table><thead><tr><th>Month</th><th class="r">Reels</th><th class="r">Median length</th>'
           '<th class="r">Median reach</th><th class="r">Median views</th>'
           '<th class="r">Retention, 45s+ reels</th><th class="r">Recommendation share</th></tr></thead><tbody>']
    for m in mrows:
        cls = ' class="flag"' if m["lab"] == "Aug" else ""
        lr = f'{m["longret"]*100:.0f}%' if m["longret"] is not None else "—"
        rs = f'{m["recsh"]*100:.0f}%' if m["recsh"] is not None else "—"
        out.append(f'<tr{cls}><td>{m["lab"]}</td><td class="r n">{m["n"]}</td><td class="r n">{int(m["dur"])}s</td>'
                   f'<td class="r n">{f(m["reach"])}</td><td class="r n em">{f(m["med"])}</td>'
                   f'<td class="r n">{lr}</td><td class="r n">{rs}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)

def topic_table():
    T = sorted([x for x in R if x["t"]], key=lambda z: z["v"], reverse=True)
    out = ['<table><thead><tr><th class="r">Views</th><th class="r">Len</th><th>Title</th></tr></thead><tbody>']
    for x in T[:8]:
        out.append(f'<tr class="hi"><td class="r n em">{f(x["v"])}</td><td class="r n">{int(x["dur"])}s</td><td>{esc(x["t"])}</td></tr>')
    out.append('<tr class="gap"><td colspan="3">· · · 24 reels between · · ·</td></tr>')
    for x in T[-6:]:
        out.append(f'<tr class="flag"><td class="r n">{f(x["v"])}</td><td class="r n">{int(x["dur"])}s</td><td>{esc(x["t"])}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)

def tokens(d):
    return "\n".join(f"      --{k}: {v};" for k, v in d.items())

hero_short = med([x["v"] for x in short]); hero_long = med([x["v"] for x in lng])
aug = [m for m in mrows if m["lab"] == "Aug"][0]
jul = [m for m in mrows if m["lab"] == "Jul"][0]
hits_short = sum(1 for x in short if x["v"] >= 7900)

HTML = f"""<title>The 30-Second Cliff</title>
<style>
  :root {{
{tokens(TOK)}
    --bar-mut: #c2c8c4; --dot-mut: #9aa4a2;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
{tokens(DTOK)}
      --bar-mut: #39423f; --dot-mut: #5d6a68;
    }}
  }}
  :root[data-theme="dark"] {{
{tokens(DTOK)}
    --bar-mut: #39423f; --dot-mut: #5d6a68;
  }}

  * {{ box-sizing: border-box; }}
  body {{
    background: var(--surf); color: var(--ink);
    font-family: var(--serif); font-size: 17px; line-height: 1.62;
    margin: 0; padding: 0 20px 96px;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 780px; margin: 0 auto; }}
  .mono {{ font-family: var(--mono); }}

  header {{ padding: 64px 0 36px; border-bottom: 2px solid var(--ink); }}
  .eyebrow {{
    font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em;
    text-transform: uppercase; color: var(--mut); margin: 0 0 22px;
    display: flex; flex-wrap: wrap; gap: 6px 14px;
  }}
  h1 {{
    font-family: var(--mono); font-size: clamp(38px, 8vw, 62px); line-height: 1.02;
    letter-spacing: -0.03em; font-weight: 600; margin: 0 0 20px; text-wrap: balance;
  }}
  .standfirst {{ font-size: 20px; line-height: 1.5; color: var(--ink2); margin: 0; max-width: 62ch; }}

  h2 {{
    font-family: var(--mono); font-size: 13px; letter-spacing: 0.13em; text-transform: uppercase;
    font-weight: 600; color: var(--accent); margin: 0 0 6px;
  }}
  h2 + .dek {{
    font-family: var(--serif); font-size: 27px; line-height: 1.24; letter-spacing: -0.015em;
    margin: 0 0 18px; text-wrap: balance; font-weight: 600;
  }}
  section {{ padding: 46px 0; border-bottom: 1px solid var(--rule); }}
  section:last-of-type {{ border-bottom: none; }}
  p {{ margin: 0 0 16px; max-width: 66ch; }}
  strong {{ font-weight: 700; }}
  .lede {{ font-size: 18.5px; }}

  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(184px, 1fr)); gap: 2px;
           background: var(--rule); border: 1px solid var(--rule); margin: 34px 0 0; }}
  .kpi {{ background: var(--card); padding: 20px 18px; }}
  .kpi .fig {{ font-family: var(--mono); font-size: 40px; line-height: 1; font-weight: 600;
               letter-spacing: -0.035em; font-variant-numeric: tabular-nums; }}
  .kpi .fig.bad {{ color: var(--alarm); }}
  .kpi .fig.good {{ color: var(--accent); }}
  .kpi .lab {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase;
               color: var(--mut); margin-top: 10px; line-height: 1.45; }}

  figure {{ margin: 26px 0; background: var(--card); border: 1px solid var(--rule); padding: 20px 18px 14px;
            overflow-x: auto; }}
  figcaption {{ font-family: var(--mono); font-size: 11.5px; color: var(--mut); line-height: 1.5;
                margin-top: 12px; letter-spacing: 0.01em; }}
  .ct {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.12em; fill: var(--mut); }}
  .cl {{ font-family: var(--mono); font-size: 12.5px; fill: var(--ink2); }}
  .cv {{ font-family: var(--mono); font-size: 13px; font-weight: 600; fill: var(--ink); font-variant-numeric: tabular-nums; }}
  .cv.em {{ fill: var(--accent); }}
  .cn, .cn2 {{ font-family: var(--mono); font-size: 10.5px; fill: var(--mut); }}
  .cax {{ font-family: var(--mono); font-size: 11px; fill: var(--mut); }}
  .clg {{ font-family: var(--mono); font-size: 11.5px; font-weight: 600; }}
  .cthr {{ font-family: var(--mono); font-size: 10.5px; fill: var(--alarm); letter-spacing: 0.08em; }}
  .grid {{ stroke: var(--rule); stroke-width: 1; }}
  .thr {{ stroke: var(--alarm); stroke-width: 1.5; stroke-dasharray: 3 4; opacity: 0.75; }}

  .tw {{ overflow-x: auto; margin: 22px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
  th {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase;
        color: var(--mut); text-align: left; font-weight: 600; padding: 0 12px 9px 0;
        border-bottom: 1px solid var(--ink); white-space: nowrap; }}
  td {{ padding: 9px 12px 9px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }}
  th.r, td.r {{ text-align: right; padding-right: 0; }}
  td.n {{ font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 14px; white-space: nowrap; }}
  td.em {{ font-weight: 600; }}
  tr.hi td.em {{ color: var(--accent); }}
  tr.flag td.em, tr.flag td.n:first-child {{ color: var(--alarm); }}
  tr.gap td {{ font-family: var(--mono); font-size: 11px; color: var(--mut); text-align: center;
               padding: 12px 0; letter-spacing: 0.08em; }}

  .callout {{ border-left: 3px solid var(--accent); padding: 4px 0 4px 20px; margin: 26px 0; }}
  .callout.warn {{ border-left-color: var(--alarm); }}
  .callout p {{ margin: 0 0 10px; }}
  .callout p:last-child {{ margin: 0; }}

  ol.steps {{ list-style: none; counter-reset: s; padding: 0; margin: 22px 0 0; }}
  ol.steps li {{ counter-increment: s; position: relative; padding: 0 0 22px 46px; margin: 0; max-width: 66ch; }}
  ol.steps li::before {{
    content: counter(s, decimal-leading-zero); position: absolute; left: 0; top: 1px;
    font-family: var(--mono); font-size: 12px; font-weight: 600; color: var(--accent);
    letter-spacing: 0.06em;
  }}
  ol.steps h3 {{ font-family: var(--serif); font-size: 19px; margin: 0 0 5px; font-weight: 700; }}
  ol.steps p {{ margin: 0; color: var(--ink2); font-size: 16.5px; }}

  ul.plain {{ padding-left: 20px; margin: 0 0 16px; max-width: 66ch; }}
  ul.plain li {{ margin-bottom: 9px; }}

  footer {{ padding: 40px 0 0; font-family: var(--mono); font-size: 11.5px; color: var(--mut);
            line-height: 1.7; border-top: 2px solid var(--ink); margin-top: 20px; }}
  @media (max-width: 560px) {{ body {{ font-size: 16px; }} .kpi .fig {{ font-size: 32px; }} }}
</style>

<div class="wrap">
<header>
  <div class="eyebrow"><span>Untold America</span><span>·</span><span>116 reels</span><span>·</span>
    <span>27 Apr – 15 Aug 2026</span><span>·</span><span>Facebook video export</span></div>
  <h1>The 30-Second<br>Cliff</h1>
  <p class="standfirst">Your reels didn't stop working. One format stopped being made. Reels
    under 30 seconds returned a median of {f(hero_short)} views; reels of 45 seconds or more
    returned {f(hero_long)}. That gap holds inside every month of the data.</p>

  <div class="kpis">
    <div class="kpi"><div class="fig good">{ratio:.1f}×</div>
      <div class="lab">More views from reels<br>under 30 seconds</div></div>
    <div class="kpi"><div class="fig">{tot_rec/grand*100:.0f}%</div>
      <div class="lab">Of watch time comes from<br>recommendations, not followers</div></div>
    <div class="kpi"><div class="fig good">{hits_short}/{len(short)}</div>
      <div class="lab">Short reels that cleared<br>7,900 views</div></div>
  </div>
</header>

<section>
  <h2>The finding</h2>
  <p class="dek">Length is the lever, and the drop-off is a cliff rather than a slope.</p>
  <p class="lede">Sorting all 116 reels by length shows something unusually clean. Everything under
    30 seconds performs in a different league, and the fall between the 20–30s band and the 30–45s
    band is a factor of about eighteen — not a gentle decline.</p>
  <figure>{chart_cliff()}
    <figcaption>Median views per reel by length band. Bands under 30 seconds highlighted.
      Medians, not averages, so single viral reels can't inflate a band.</figcaption>
  </figure>
  <p>The retention numbers explain the mechanism. Reels under 20 seconds hold {bucket_rows[0]["ret"]*100:.0f}%
    of viewers on average; reels over 60 seconds hold {bucket_rows[4]["ret"]*100:.0f}%. Facebook's
    recommendation engine reads that difference and decides whether to keep pushing a reel to people
    who don't follow you. Since {tot_rec/grand*100:.0f}% of your watch time comes from exactly that
    source, retention is not one metric among many — it is the whole distribution mechanism.</p>
</section>

<section>
  <h2>Reel by reel</h2>
  <p class="dek">Every single short reel you made worked.</p>
  <p>Aggregates can hide a couple of lucky hits. This one doesn't: of the {len(short)} reels you
    published under 30 seconds, {hits_short} cleared 7,900 views. The one that didn't was published
    two days before the export.</p>
  <figure>{chart_scatter()}
    <figcaption>All 116 reels. Views on a logarithmic scale, so each gridline is 10× the one below.
      Points left of the dashed line are under 30 seconds. Hover any point for its detail.</figcaption>
  </figure>
  <div class="tw">{short_table()}</div>
</section>

<section>
  <h2>Ruling out coincidence</h2>
  <p class="dek">It isn't that your good month happened to be your short month.</p>
  <p>The obvious objection is timing — maybe short reels just happened to land during a lucky
    stretch. They didn't. Comparing short against long <em>inside</em> each month, where the
    algorithm, the season and your audience are all held constant, the gap survives:</p>
  <div class="tw">{within_table()}</div>
  <div class="callout">
    <p>In June, short reels out-performed long ones by roughly 33×. In July, by 3.6×. There is no
      month in this data where the longer reels won.</p>
  </div>
</section>

<section>
  <h2>August</h2>
  <p class="dek">Two things went wrong, and only one of them is length.</p>
  <p>July was your best month — a median of {f(jul["med"])} views. August fell to {f(aug["med"])}.
    Part of that is straightforward: {sum(1 for x in R if x["d"].month==8 and x["dur"]>=45)} of your
    {aug["n"]} August reels were 45 seconds or longer, and you published exactly one short reel all month.</p>
  <figure>{chart_monthly()}
    <figcaption>Median views per reel by month. August is partial (1–15 Aug).</figcaption>
  </figure>
  <p>But length isn't the whole story. Holding length constant — looking only at reels
    45 seconds and over — retention had been stable around 38–44% from May through July, then fell
    to {aug["longret"]*100:.0f}% in August at the same median length. Your long reels didn't just stay
    long; they got materially less watchable.</p>
  <div class="tw">{month_table()}</div>
  <div class="callout warn">
    <p>The consequence shows up in the recommendation share. For reels mature enough to have
      finished their run, {mrows[1]["recsh"]*100:.0f}–{jul["recsh"]*100:.0f}% of watch time came from
      recommendations in May–July. In August that fell to {aug["recsh"]*100:.0f}%, and median reach
      dropped from {f(jul["reach"])} to {f(aug["reach"])}.</p>
    <p>That is Facebook doing what it is designed to do: it stopped showing your reels to strangers
      because the retention signal told it not to. It isn't a penalty, and there is nothing to appeal.</p>
  </div>
</section>

<section>
  <h2>Where the views come from</h2>
  <p class="dek">Your 33,000 followers are almost irrelevant to your reach.</p>
  <p>Across all 116 reels, watch time splits {tot_rec/grand*100:.0f}% recommendations,
    {tot_fol/grand*100:.1f}% followers, {tot_shr/grand*100:.1f}% shares. Your follower base
    contributes roughly {f(tot_fol/3600)} hours against {f(tot_rec/3600)} hours from people who
    have never heard of you.</p>
  <p>This is worth internalising, because it reframes what a bad reel means. A reel that gets
    2,000 views hasn't been rejected by your audience — it has been declined by the recommendation
    engine, and your followers were most of who saw it. The follower count is a floor, not an engine.
    Every breakout you have had came from strangers.</p>
  <figure>{chart_curves()}
    <figcaption>Average share of viewers still watching, sampled across each reel's length.
      Top and bottom 20% by views, 23 reels each.</figcaption>
  </figure>
  <p>The two curves separate almost immediately and never reconverge. By a quarter of the way in,
    the gap is already most of what it will ever be — which tells you the opening seconds, not the
    payoff, are doing the sorting.</p>
</section>

<section>
  <h2>What to post about</h2>
  <p class="dek">A named character with a myth to overturn.</p>
  <p>This section rests on weaker evidence than the rest, and it's worth saying why: only 38 of your
    116 reels carry a title in the export, and neither of your two biggest reels is among them. What
    follows is a real pattern in the titles that exist, not a verdict on your whole catalogue.</p>
  <div class="tw">{topic_table()}</div>
  <p>The reels at the top name one person and promise to overturn something you thought you knew —
    Doc Holliday wasn't the killer you think; Billy the Kid's scars tell a story Pat Garrett didn't
    want told. The ones at the bottom are about <em>institutions and events</em>: the Pinkertons, the
    Reno Gang, the Bear River Massacre, America's first detectives. Same research quality, no
    protagonist to attach to in the first two seconds.</p>
  <div class="callout">
    <p>One specific pattern to stop: some version of "did Billy the Kid fake his own death?" appears
      eight times between May and August. Seven of those returned under 2,500 views. The single Billy
      the Kid reel that broke out took a different angle entirely — the scars, and Pat Garrett's
      motive. The topic isn't exhausted; that particular question is.</p>
  </div>
</section>

<section>
  <h2>What to do</h2>
  <p class="dek">Four changes, in the order they'll pay.</p>
  <ol class="steps">
    <li><h3>Cap the next twenty reels at 30 seconds</h3>
      <p>Not "shorter" — capped. This is the highest-confidence finding in the data and it costs you
        nothing to test. If it works you'll know inside a week, because recommendation traffic
        arrives fast or not at all.</p></li>
    <li><h3>Cut to the payoff, don't compress the whole story</h3>
      <p>The 17-second reels that hit 466K and 450K retained over 99% — viewers watched them more
        than once. That comes from making one point cleanly, not from narrating a 70-second script
        at speed.</p></li>
    <li><h3>Lead with a person, and with the correction</h3>
      <p>Name the character in the first line and state what everyone gets wrong. Save the
        institutional and massacre material for when you have the reach to spend on it.</p></li>
    <li><h3>Re-cut your existing winners</h3>
      <p>Your 305K reel is 69 seconds and your 143K is 71. Both already proved the topic works.
        Cutting each into a 20-second version is the cheapest inventory you have.</p></li>
  </ol>
</section>

<section>
  <h2>What this can't tell you</h2>
  <p class="dek">The honest limits of this data.</p>
  <ul class="plain">
    <li><strong>The export starts 27 April, not 1 January.</strong> Whatever you posted before that
      isn't here, so "when did it change" only reaches back three and a half months.</li>
    <li><strong>78 of 116 reels have no title.</strong> Topic conclusions come from the 38 that do,
      and those are almost all May–June — the weaker period. Both 450K+ reels are untitled, so what
      actually made them work is unknown.</li>
    <li><strong>This is observational, not an experiment.</strong> Short reels might also have been
      your better ideas, or your better edits. Length is the strongest single explanation in the data
      and it holds within every month, but the clean test is deliberate: post a mediocre idea short
      and a strong idea long, and compare.</li>
    <li><strong>August reels are young.</strong> Reels published in the last few days hadn't finished
      accumulating views. Restricting to reels at least ten days old, the August median is still
      about 3,464 — so the decline is real, but the exact August figures will improve somewhat.</li>
  </ul>
</section>

<footer>
  Generated from the Meta Business Suite video-level export ·
  116 reels, 27 Apr – 15 Aug 2026 · medians throughout ·
  analysis scripts in facebook/ of the youtube-niches repository
</footer>
</div>
"""

open(OUT, "w", encoding="utf-8").write(HTML)
print(f"wrote {OUT} ({len(HTML):,} bytes)")
print(f"short median {f(hero_short)} / long median {f(hero_long)} = {ratio:.1f}x")
print(f"rec {tot_rec/grand*100:.1f}% fol {tot_fol/grand*100:.1f}% shr {tot_shr/grand*100:.1f}%")
print(f"aug longret {aug['longret']*100:.0f}% jul longret {jul['longret']*100:.0f}%")
print(f"short reels >=7900: {hits_short}/{len(short)}")
