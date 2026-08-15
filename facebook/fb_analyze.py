#!/usr/bin/env python3
"""Diagnose a Facebook Page's reel performance: what to post about, and in what format.

Accepts either the JSON produced by fb_pull.py, or a Meta Business Suite content
export (CSV). Emits a markdown report.

Usage:
    python3 fb_analyze.py data/page_data.json -o report.md
    python3 fb_analyze.py "Content Export.csv" -o report.md

Only the standard library is used.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Metric resolution
#
# Meta renames reel metrics between Graph API versions and between the API and
# the Business Suite export, so every canonical field is resolved from a
# priority-ordered list of candidates rather than a single hard-coded name.
# ---------------------------------------------------------------------------

VIEW_METRICS = [
    "fb_reels_total_plays",
    "blue_reels_play_count",
    "post_video_views",
    "total_video_views",
    "post_video_views_organic",
    "total_video_views_organic",
]

REACH_METRICS = [
    "post_impressions_unique",
    "total_video_impressions_unique",
    "post_video_views_unique",
    "total_video_views_unique",
    "post_impressions",
    "total_video_impressions",
]

AVG_WATCH_METRICS = [
    "post_video_avg_time_watched",
    "total_video_avg_time_watched",
]

TOTAL_WATCH_METRICS = [
    "post_video_view_time",
    "total_video_view_total_time",
    "post_video_view_time_organic",
]

FOLLOW_METRICS = [
    "post_video_followers",
    "fb_reels_total_follows",
    "blue_reels_follows",
]

COMPLETE_METRICS = [
    "post_video_complete_views_organic",
    "total_video_complete_views",
    "post_video_complete_views_30s",
]

REACTION_METRICS = [
    "total_video_reactions_by_type_total__sum",
    "post_video_likes_by_reaction_type__sum",
    "post_reactions_by_type_total__sum",
]

# Graph API reports these in milliseconds.
MS_METRICS = set(AVG_WATCH_METRICS) | set(TOTAL_WATCH_METRICS)

STOPWORDS = set("""
a an the and or but if then than that this these those of in on at to for with from by
is are was were be been being am do does did doing have has had having i you he she it
we they me him her them my your his its our their as so no not just now new get got
about into out up down over under again more most some any all can will would should
could what when where who whom which how why very really too also here there
""".split())


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def percentile(values, pct):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    pos = (len(vals) - 1) * pct / 100.0
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(vals[int(pos)])
    return vals[low] + (vals[high] - vals[low]) * (pos - low)


def _ranks(values):
    """Average-tied ranks, for Spearman."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Rank correlation. Returns None when there isn't enough spread to judge."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 6:
        return None
    xr = _ranks([p[0] for p in pairs])
    yr = _ranks([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(xr) / n, sum(yr) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xr, yr))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xr))
    dy = math.sqrt(sum((b - my) ** 2 for b in yr))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def fmt(n, decimals=0):
    if n is None:
        return "—"
    if isinstance(n, float) and decimals:
        return f"{n:,.{decimals}f}"
    return f"{round(n):,}"


def pct(n, decimals=1):
    return "—" if n is None else f"{n * 100:.{decimals}f}%"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class Post:
    __slots__ = ("id", "date", "text", "duration", "views", "reach", "avg_watch",
                 "total_watch", "follows", "completes", "reactions", "comments",
                 "shares", "url")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))

    @property
    def engagement(self):
        parts = [self.reactions, self.comments, self.shares]
        vals = [p for p in parts if p is not None]
        return sum(vals) if vals else None

    @property
    def engagement_rate(self):
        eng, views = self.engagement, self.views
        if eng is None or not views:
            return None
        return eng / views

    @property
    def follow_rate(self):
        if self.follows is None or not self.views:
            return None
        return self.follows / self.views

    @property
    def share_rate(self):
        if self.shares is None or not self.views:
            return None
        return self.shares / self.views

    @property
    def comment_rate(self):
        if self.comments is None or not self.views:
            return None
        return self.comments / self.views

    @property
    def retention(self):
        """Fraction of the reel the average viewer watched."""
        if not self.avg_watch or not self.duration:
            return None
        return min(self.avg_watch / self.duration, 5.0)  # >1 means loops


def pick(metrics, candidates):
    for name in candidates:
        if name in metrics:
            value = metrics[name]
            if isinstance(value, (int, float)):
                if name in MS_METRICS:
                    value = value / 1000.0
                return float(value)
    return None


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)

    posts = []
    for video in payload.get("videos", []):
        metrics = video.get("insights") or {}
        if "_error" in metrics:
            metrics = {}
        created = video.get("created_time")
        date = None
        if created:
            try:
                date = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                pass
        text = " ".join(filter(None, [video.get("title"), video.get("description")]))
        posts.append(Post(
            id=video.get("id"),
            date=date,
            text=text,
            duration=video.get("length"),
            views=pick(metrics, VIEW_METRICS),
            reach=pick(metrics, REACH_METRICS),
            avg_watch=pick(metrics, AVG_WATCH_METRICS),
            total_watch=pick(metrics, TOTAL_WATCH_METRICS),
            follows=pick(metrics, FOLLOW_METRICS),
            completes=pick(metrics, COMPLETE_METRICS),
            reactions=pick(metrics, REACTION_METRICS),
            comments=None,
            shares=None,
            url=video.get("permalink_url"),
        ))

    # Fold engagement counts from the posts edge onto the matching video.
    by_post_id = {}
    for video, post in zip(payload.get("videos", []), posts):
        if video.get("post_id"):
            by_post_id[video["post_id"]] = post
    for raw in payload.get("posts", []):
        target = by_post_id.get(raw.get("id"))
        if not target:
            continue
        summary = ((raw.get("comments") or {}).get("summary") or {})
        target.comments = summary.get("total_count")
        reactions = ((raw.get("reactions") or {}).get("summary") or {})
        if reactions.get("total_count") is not None:
            target.reactions = reactions["total_count"]
        target.shares = (raw.get("shares") or {}).get("count")
        if not target.text:
            target.text = raw.get("message") or ""

    page = payload.get("page", {})
    return posts, page


def find_col(headers, *token_sets):
    """Find the header best matching any token set (all tokens must appear).

    Among headers that match, the shortest wins. Meta ships combined columns
    like "Reactions, comments and shares" alongside the individual "Reactions",
    "Comments" and "Shares" columns; a first-match rule would bind all three
    names to the combined column and triple-count engagement.
    """
    lowered = {h: h.lower() for h in headers}
    for tokens in token_sets:
        matches = [h for h, low in lowered.items() if all(tok in low for tok in tokens)]
        if matches:
            exact = [h for h in matches if lowered[h].strip() == " ".join(tokens)]
            return exact[0] if exact else min(matches, key=len)
    return None


def to_num(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_csv(path):
    """Load a Meta Business Suite content export.

    Column names differ by locale and export version, so headers are matched
    fuzzily rather than by exact string.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(fh, dialect=dialect))

    if not rows:
        raise SystemExit(f"{path}: no rows found")

    # Business Suite sometimes emits a second header row of descriptions.
    if rows and all(to_num(v) is None for v in rows[0].values()):
        joined = " ".join(str(v).lower() for v in rows[0].values())
        if "date" in joined or "number" in joined or "the " in joined:
            rows = rows[1:]

    headers = list(rows[0].keys())
    col = {
        "date": find_col(headers, ("publish", "time"), ("date",), ("created",)),
        "title": find_col(headers, ("title",), ("description",), ("caption",),
                          ("message",)),
        "duration": find_col(headers, ("duration",), ("length",)),
        "views": find_col(headers, ("views",), ("plays",), ("impressions",)),
        "reach": find_col(headers, ("reach",), ("accounts", "reached")),
        "avg_watch": find_col(headers, ("average", "second"), ("avg", "watch"),
                              ("average", "watch")),
        "reactions": find_col(headers, ("reactions",), ("likes",)),
        "comments": find_col(headers, ("comments",)),
        "shares": find_col(headers, ("shares",)),
        "follows": find_col(headers, ("follows",), ("new", "followers")),
        "url": find_col(headers, ("permalink",), ("link",), ("url",)),
        "id": find_col(headers, ("post", "id"), ("id",)),
    }
    # Total watch time must not collapse onto the average-watch column, which
    # "seconds viewed" would otherwise match ("Average seconds viewed").
    col["total_watch"] = find_col(
        [h for h in headers if h != col["avg_watch"]],
        ("seconds", "viewed"), ("watch", "time"), ("minutes", "viewed"),
    )

    resolved = {k: v for k, v in col.items() if v}
    print(f"CSV columns matched: {resolved}", file=sys.stderr)
    if not col["views"]:
        raise SystemExit(
            f"{path}: could not find a views/plays column. Headers were:\n  "
            + "\n  ".join(headers)
        )

    posts = []
    for row in rows:
        date = None
        if col["date"] and row.get(col["date"]):
            raw = str(row[col["date"]]).strip()
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                            "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M",
                            "%d/%m/%Y %H:%M", "%Y-%m-%d", "%m/%d/%Y",
                            "%d/%m/%Y"):
                try:
                    date = datetime.strptime(raw[:len(datetime.now()
                                                      .strftime(pattern))], pattern)
                    date = date.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        total_watch = to_num(row.get(col["total_watch"])) if col["total_watch"] else None
        if total_watch is not None and col["total_watch"] and \
                "minute" in col["total_watch"].lower():
            total_watch *= 60

        posts.append(Post(
            id=row.get(col["id"]) if col["id"] else None,
            date=date,
            text=row.get(col["title"], "") if col["title"] else "",
            duration=to_num(row.get(col["duration"])) if col["duration"] else None,
            views=to_num(row.get(col["views"])),
            reach=to_num(row.get(col["reach"])) if col["reach"] else None,
            avg_watch=to_num(row.get(col["avg_watch"])) if col["avg_watch"] else None,
            total_watch=total_watch,
            follows=to_num(row.get(col["follows"])) if col["follows"] else None,
            completes=None,
            reactions=to_num(row.get(col["reactions"])) if col["reactions"] else None,
            comments=to_num(row.get(col["comments"])) if col["comments"] else None,
            shares=to_num(row.get(col["shares"])) if col["shares"] else None,
            url=row.get(col["url"]) if col["url"] else None,
        ))

    return [p for p in posts if p.views is not None], {}


# ---------------------------------------------------------------------------
# Analysis sections
# ---------------------------------------------------------------------------

def section_overview(posts, page, out):
    dated = [p for p in posts if p.date]
    views = [p.views for p in posts if p.views is not None]
    out.append("## 1. What you're working with\n")
    if page.get("name"):
        followers = page.get("followers_count") or page.get("fan_count")
        out.append(f"**Page:** {page['name']} — {fmt(followers)} followers\n")
    out.append(f"- Reels/videos analysed: **{len(posts)}**")
    if dated:
        span = f"{min(p.date for p in dated):%Y-%m-%d} → {max(p.date for p in dated):%Y-%m-%d}"
        out.append(f"- Date range: **{span}**")
    if views:
        out.append(f"- Median views: **{fmt(median(views))}**  ·  "
                   f"p90: **{fmt(percentile(views, 90))}**  ·  "
                   f"best: **{fmt(max(views))}**")
        total = sum(views)
        top10 = sum(sorted(views, reverse=True)[:max(1, len(views) // 10)])
        out.append(f"- Share of all views from your top 10% of reels: "
                   f"**{pct(top10 / total)}**")
    out.append("")


def section_timeline(posts, out):
    dated = [p for p in posts if p.date and p.views is not None]
    if len(dated) < 6:
        return
    out.append("## 2. When it changed\n")
    out.append("Monthly medians — the median is the honest number here, because a "
               "single 500K outlier drags an average upward and hides a declining floor.\n")
    buckets = defaultdict(list)
    for p in dated:
        buckets[(p.date.year, p.date.month)].append(p)

    out.append("| Month | Reels | Median views | Best | Median ER | Median retention |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for key in sorted(buckets):
        group = buckets[key]
        vs = [p.views for p in group]
        ers = [p.engagement_rate for p in group if p.engagement_rate is not None]
        rets = [p.retention for p in group if p.retention is not None]
        out.append(
            f"| {key[0]}-{key[1]:02d} | {len(group)} | {fmt(median(vs))} | "
            f"{fmt(max(vs))} | {pct(median(ers)) if ers else '—'} | "
            f"{pct(median(rets)) if rets else '—'} |"
        )
    out.append("")

    months = sorted(buckets)
    if len(months) >= 4:
        early = [p.views for k in months[: len(months) // 2] for p in buckets[k]]
        late = [p.views for k in months[len(months) // 2:] for p in buckets[k]]
        me, ml = median(early), median(late)
        if me and ml:
            change = (ml - me) / me
            direction = "down" if change < 0 else "up"
            out.append(f"**First half vs second half:** median views went from "
                       f"{fmt(me)} to {fmt(ml)} — {direction} {pct(abs(change))}.\n")


def section_drivers(posts, out):
    """Which measurable signals actually track with views."""
    out.append("## 3. What actually correlates with views\n")
    views = [p.views for p in posts]
    tests = [
        ("Retention (avg watch ÷ duration)", [p.retention for p in posts]),
        ("Average watch time (seconds)", [p.avg_watch for p in posts]),
        ("Duration (seconds)", [p.duration for p in posts]),
        ("Engagement rate", [p.engagement_rate for p in posts]),
        # Rates, not raw counts: a reel with 10x the views mechanically collects
        # ~10x the shares, so correlating raw shares against views is circular.
        ("Share rate (shares ÷ views)", [p.share_rate for p in posts]),
        ("Comment rate (comments ÷ views)", [p.comment_rate for p in posts]),
        ("Follow rate (follows ÷ views)", [p.follow_rate for p in posts]),
        ("Caption length (chars)", [len(p.text or "") for p in posts]),
        ("Hashtag count", [len(re.findall(r"#\w+", p.text or "")) for p in posts]),
    ]
    rows = []
    for label, xs in tests:
        rho = spearman(xs, views)
        if rho is not None:
            rows.append((abs(rho), label, rho))
    if not rows:
        out.append("_Not enough data with these fields populated to correlate._\n")
        return
    rows.sort(reverse=True)
    out.append("| Signal | Rank correlation with views | Read |")
    out.append("|---|---:|---|")
    for _, label, rho in rows:
        if abs(rho) >= 0.5:
            read = "strong"
        elif abs(rho) >= 0.3:
            read = "moderate"
        elif abs(rho) >= 0.15:
            read = "weak"
        else:
            read = "no real signal"
        read = f"{read} {'positive' if rho > 0 else 'negative'}" \
            if read != "no real signal" else read
        out.append(f"| {label} | {rho:+.2f} | {read} |")
    out.append("")
    out.append("Correlation is not proof of cause, but on short-form the causal arrow "
               "is well established for one of these: retention drives distribution, "
               "not the other way round.\n")


def bucket_table(posts, key_fn, labels, out, title, note=""):
    groups = defaultdict(list)
    for p in posts:
        key = key_fn(p)
        if key is not None:
            groups[key].append(p)
    if len(groups) < 2:
        return False
    out.append(f"### {title}\n")
    if note:
        out.append(note + "\n")
    overall = median([p.views for p in posts if p.views is not None]) or 0
    out.append("| Bucket | Reels | Median views | vs page median | Median retention |")
    out.append("|---|---:|---:|---:|---:|")
    for key in labels:
        if key not in groups:
            continue
        group = groups[key]
        med = median([p.views for p in group])
        rets = [p.retention for p in group if p.retention is not None]
        lift = (med / overall - 1) if overall and med else None
        out.append(f"| {key} | {len(group)} | {fmt(med)} | "
                   f"{('%+.0f%%' % (lift * 100)) if lift is not None else '—'} | "
                   f"{pct(median(rets)) if rets else '—'} |")
    out.append("")
    return True


def section_format(posts, out):
    out.append("## 4. Format\n")

    def duration_bucket(p):
        if not p.duration:
            return None
        d = p.duration
        if d < 10:
            return "under 10s"
        if d < 20:
            return "10–20s"
        if d < 30:
            return "20–30s"
        if d < 45:
            return "30–45s"
        if d < 60:
            return "45–60s"
        if d < 90:
            return "60–90s"
        return "90s+"

    order = ["under 10s", "10–20s", "20–30s", "30–45s", "45–60s", "60–90s", "90s+"]
    had_duration = bucket_table(
        posts, duration_bucket, order, out, "Length",
        "Where median views peak is your format sweet spot. If retention falls off a "
        "cliff a bucket before views do, the cliff is the cause."
    )
    if not had_duration:
        out.append("_No duration data available — the Business Suite export includes "
                   "it, the Graph `length` field covers it for reels._\n")

    def caption_bucket(p):
        n = len(p.text or "")
        if n == 0:
            return "no caption"
        if n < 50:
            return "under 50 chars"
        if n < 120:
            return "50–120 chars"
        if n < 250:
            return "120–250 chars"
        return "250+ chars"

    bucket_table(posts, caption_bucket,
                 ["no caption", "under 50 chars", "50–120 chars", "120–250 chars",
                  "250+ chars"], out, "Caption length")

    def hashtag_bucket(p):
        n = len(re.findall(r"#\w+", p.text or ""))
        if n == 0:
            return "none"
        if n <= 3:
            return "1–3"
        if n <= 8:
            return "4–8"
        return "9+"

    bucket_table(posts, hashtag_bucket, ["none", "1–3", "4–8", "9+"], out, "Hashtags")


def section_timing(posts, out):
    dated = [p for p in posts if p.date and p.views is not None]
    if len(dated) < 12:
        return
    out.append("## 5. Timing and cadence\n")
    out.append("_Times are UTC as stored by the API — shift to your own timezone._\n")

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    bucket_table(dated, lambda p: days[p.date.weekday()], days, out, "Day of week")

    def hour_bucket(p):
        h = p.date.hour
        return f"{h // 3 * 3:02d}:00–{h // 3 * 3 + 3:02d}:00"

    hours = [f"{h:02d}:00–{h + 3:02d}:00" for h in range(0, 24, 3)]
    bucket_table(dated, hour_bucket, hours, out, "Hour of day (3h blocks)")

    # Posting cadence vs performance — the over-posting dilution check.
    weeks = defaultdict(list)
    for p in dated:
        iso = p.date.isocalendar()
        weeks[(iso[0], iso[1])].append(p)
    if len(weeks) >= 6:
        counts, med_views = [], []
        for group in weeks.values():
            counts.append(len(group))
            med_views.append(median([p.views for p in group]))
        rho = spearman(counts, med_views)
        avg_per_week = sum(counts) / len(counts)
        out.append(f"### Cadence\n")
        out.append(f"You post **{avg_per_week:.1f} reels/week** on average "
                   f"across {len(weeks)} weeks.")
        if rho is not None:
            if rho < -0.3:
                out.append(f"Weeks where you posted *more* had **lower** median views "
                           f"(rho {rho:+.2f}) — that's a volume-over-quality signal: "
                           f"you're splitting the same demand across more uploads.")
            elif rho > 0.3:
                out.append(f"Weeks where you posted more also had higher median views "
                           f"(rho {rho:+.2f}) — volume is not hurting you.")
            else:
                out.append(f"Posting volume shows no clear relationship to median "
                           f"views (rho {rho:+.2f}).")
        out.append("")


def tokenize(text):
    words = re.findall(r"[a-z']{3,}", (text or "").lower())
    return [w for w in words if w not in STOPWORDS]


def section_topics(posts, out, min_posts=3):
    """Find caption terms whose reels beat the page median, with enough samples."""
    scored = [p for p in posts if p.views is not None and (p.text or "").strip()]
    if len(scored) < 10:
        out.append("## 6. Topics\n")
        out.append("_Too few reels with caption text to run topic lift. This section "
                   "needs ~20+ captioned reels to say anything trustworthy._\n")
        return

    overall = median([p.views for p in scored])
    term_posts = defaultdict(list)
    for p in scored:
        for term in set(tokenize(p.text)):
            term_posts[term].append(p)
    for p in scored:
        for tag in set(t.lower() for t in re.findall(r"#\w+", p.text or "")):
            term_posts[tag].append(p)

    rows = []
    for term, group in term_posts.items():
        if len(group) < min_posts:
            continue
        med = median([p.views for p in group])
        others = [p.views for p in scored if p not in group]
        med_other = median(others)
        if not med or not med_other:
            continue
        rows.append((med / med_other, term, len(group), med))

    out.append("## 6. Topics that over- and under-perform\n")
    if not rows:
        out.append(f"_No term appeared in at least {min_posts} reels._\n")
        return

    out.append(f"Median views of reels containing a term vs reels without it. "
               f"Page median is **{fmt(overall)}**. Terms need ≥{min_posts} reels to "
               f"appear, so treat the small-sample rows as leads to test, not proof.\n")
    rows.sort(reverse=True)

    # "topic" and "#topic" usually describe the same reels; keep one of each pair.
    deduped, seen_signatures = [], set()
    for row in rows:
        signature = (row[1].lstrip("#"), row[2], row[3])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(row)
    rows = deduped

    out.append("**Over-performing**\n")
    out.append("| Term | Reels | Median views | Lift |")
    out.append("|---|---:|---:|---:|")
    for lift, term, n, med in rows[:15]:
        if lift <= 1.15:
            break
        out.append(f"| {term} | {n} | {fmt(med)} | {lift:.2f}× |")
    out.append("")

    out.append("**Under-performing**\n")
    out.append("| Term | Reels | Median views | Lift |")
    out.append("|---|---:|---:|---:|")
    for lift, term, n, med in reversed(rows[-15:]):
        if lift >= 0.85:
            break
        out.append(f"| {term} | {n} | {fmt(med)} | {lift:.2f}× |")
    out.append("")


def section_extremes(posts, out, n=10):
    ranked = sorted([p for p in posts if p.views is not None],
                    key=lambda p: p.views, reverse=True)
    if len(ranked) < 4:
        return
    out.append("## 7. Your best and worst reels\n")
    out.append("Study the top block for what to repeat. The bottom block is where "
               "the diagnosis lives — the pattern you keep repeating that doesn't work.\n")

    for label, group in (("Top", ranked[:n]), ("Bottom", ranked[-n:])):
        out.append(f"**{label} {len(group)}**\n")
        out.append("| Views | Retention | ER | Length | Caption |")
        out.append("|---:|---:|---:|---:|---|")
        for p in group:
            caption = re.sub(r"\s+", " ", (p.text or ""))[:80]
            out.append(
                f"| {fmt(p.views)} | {pct(p.retention) if p.retention else '—'} | "
                f"{pct(p.engagement_rate) if p.engagement_rate else '—'} | "
                f"{fmt(p.duration) + 's' if p.duration else '—'} | {caption} |"
            )
        out.append("")


def section_actions(posts, out):
    """Turn the numbers above into instructions, not observations."""
    out.append("## 8. What to do next\n")
    actions = []

    views = [p.views for p in posts if p.views is not None]
    overall = median(views) if views else None

    rets = [(p.retention, p.views) for p in posts
            if p.retention is not None and p.views is not None]
    if len(rets) >= 8:
        med_ret = median([r for r, _ in rets])
        if med_ret is not None and med_ret < 0.55:
            actions.append(
                f"**Fix the hook before anything else.** Median retention is "
                f"{pct(med_ret)} — most viewers leave before the payoff, which is "
                f"exactly the signal that caps distribution. Rebuild the first 2 "
                f"seconds: state the payoff immediately instead of introducing it."
            )
        elif med_ret is not None and med_ret > 0.85:
            actions.append(
                f"**Retention is not your problem** ({pct(med_ret)} median). Your "
                f"ceiling is topic reach, not watch-through — widen the subject "
                f"matter rather than re-editing."
            )

    durs = [(p.duration, p.views) for p in posts
            if p.duration and p.views is not None]
    if len(durs) >= 10:
        rho = spearman([d for d, _ in durs], [v for _, v in durs])
        if rho is not None and rho < -0.25:
            actions.append(
                f"**Cut length.** Shorter reels correlate with more views "
                f"(rho {rho:+.2f}). Take your next 5 reels and remove every second "
                f"before the first visual payoff."
            )
        elif rho is not None and rho > 0.25:
            actions.append(
                f"**Longer is working for you** (rho {rho:+.2f}) — Facebook is "
                f"rewarding watch time here. Stop cutting for brevity."
            )

    dated = [p for p in posts if p.date and p.views is not None]
    if len(dated) >= 12:
        recent_cut = max(p.date for p in dated) - timedelta(days=30)
        recent = [p.views for p in dated if p.date >= recent_cut]
        older = [p.views for p in dated if p.date < recent_cut]
        if len(recent) >= 3 and len(older) >= 3:
            mr, mo = median(recent), median(older)
            if mr and mo:
                ratio = mr / mo
                if ratio < 0.75:
                    actions.append(
                        f"**The decline is real, not a perception.** Last 30 days "
                        f"median {fmt(mr)} vs {fmt(mo)} before — a "
                        f"{pct(1 - ratio)} drop. Treat this as a content problem to "
                        f"solve, not a penalty to wait out: nothing recovers on its "
                        f"own."
                    )
                elif ratio > 1.25:
                    actions.append(
                        f"**You are recovering.** Last 30 days median {fmt(mr)} vs "
                        f"{fmt(mo)} before — up {pct(ratio - 1)}. Whatever changed "
                        f"recently is working; hold the format steady."
                    )
                else:
                    actions.append(
                        f"**Recent output is flat, not falling.** Last 30 days median "
                        f"{fmt(mr)} vs {fmt(mo)} before. The plateau is the problem to "
                        f"attack — small edits will not move a stable baseline, a "
                        f"different topic or format will."
                    )

    ers = [p.engagement_rate for p in posts if p.engagement_rate is not None]
    if ers and overall:
        med_er = median(ers)
        if med_er is not None and med_er < 0.01:
            actions.append(
                f"**Engagement rate is thin** ({pct(med_er)}). Reels that get shared "
                f"and commented travel; add one explicit reason to comment (an "
                f"opinion to disagree with, a question with a real answer) per reel."
            )

    shares = [(p.share_rate, p.views) for p in posts
              if p.share_rate is not None and p.views is not None]
    if len(shares) >= 8:
        rho = spearman([s for s, _ in shares], [v for _, v in shares])
        if rho is not None and rho > 0.3:
            actions.append(
                f"**Shares are your distribution engine** — reels shared at a higher "
                f"*rate* reach more people (rho {rho:+.2f}), independent of how big "
                f"they got. Optimise for the 'send this to someone' reaction "
                f"specifically; it is the clearest lever in your data."
            )

    if not actions:
        populated = sum(1 for p in posts if p.retention is not None)
        if populated < len(posts) / 4:
            actions.append(
                "Most diagnostic fields are empty in this input. Re-run with the "
                "Graph API pull (`fb_pull.py`) so retention, follows and share "
                "counts are present — those three carry most of the signal."
            )
        else:
            actions.append(
                "The data is complete, and no single metric crosses a threshold "
                "worth flagging: retention, length and engagement are all mid-range. "
                "That points away from a mechanical fix and toward topic selection — "
                "work section 6 (topic lift) and section 7 (your best reels) rather "
                "than adjusting format."
            )

    for i, action in enumerate(actions, 1):
        out.append(f"{i}. {action}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="page_data.json from fb_pull.py, or a Business Suite CSV")
    ap.add_argument("-o", "--out", help="Write markdown report here (default: stdout)")
    ap.add_argument("--min-topic-posts", type=int, default=3,
                    help="Minimum reels a term must appear in (default 3)")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"not found: {args.input}")

    if args.input.lower().endswith(".json"):
        posts, page = load_json(args.input)
    else:
        posts, page = load_csv(args.input)

    posts = [p for p in posts if p.views is not None]
    if not posts:
        raise SystemExit("No reels with view counts found in the input.")

    out = [f"# Facebook Reels performance report",
           f"_Generated {datetime.now(timezone.utc):%Y-%m-%d} from "
           f"`{os.path.basename(args.input)}`_\n"]

    section_overview(posts, page, out)
    section_timeline(posts, out)
    section_drivers(posts, out)
    section_format(posts, out)
    section_timing(posts, out)
    section_topics(posts, out, min_posts=args.min_topic_posts)
    section_extremes(posts, out)
    section_actions(posts, out)

    report = "\n".join(out)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
