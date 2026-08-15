#!/usr/bin/env python3
"""Deep-dive analysis for Meta's *video-level* content export.

The video-level export ("Content level: Video" in the Export metric data dialog)
carries three things the post-level export does not, and they are the three that
actually explain reel distribution:

  * watch time split by traffic source (Recommendations / Followers / Shares)
  * a per-video retention curve
  * reach alongside views, so view-through can be separated from delivery

Usage:
    python3 fb_deepdive.py export.csv -o deepdive.md

Standard library only.
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

INTERVAL_RE = re.compile(r"Percentage of total views at interval (\d+)")


def num(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "—", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_time(raw):
    """Meta's video export writes MM/DD/YYYY HH:MM."""
    raw = (raw or "").strip()
    for pattern in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    return None


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def fmt(n):
    return "—" if n is None else f"{round(n):,}"


def pct(n, decimals=1):
    return "—" if n is None else f"{n * 100:.{decimals}f}%"


class Reel:
    def __init__(self, row, interval_cols):
        self.title = (row.get("Title") or "").strip()
        self.duration = num(row.get("Duration (sec)"))
        self.date = parse_time(row.get("Publish time"))
        self.reach = num(row.get("Reach"))
        self.views = num(row.get("3-second video views"))
        self.seconds = num(row.get("Seconds viewed"))
        self.avg_seconds = num(row.get("Average Seconds viewed"))
        self.reactions = num(row.get("Reactions"))
        self.comments = num(row.get("Comments"))
        self.shares = num(row.get("Shares"))
        self.rec_seconds = num(row.get("Seconds viewed from Recommendations")) or 0.0
        self.fol_seconds = num(row.get("Seconds viewed from Followers")) or 0.0
        self.shr_seconds = num(row.get("Seconds viewed from Shares")) or 0.0

        # Retention curve: values are the fraction of views still present at each
        # interval. Interval count varies (41 max), so store normalised positions.
        curve = []
        for idx, col in interval_cols:
            value = num(row.get(col))
            if value is None:
                break
            curve.append(value)
        self.curve = curve

    @property
    def rec_share(self):
        """Fraction of watch time delivered by recommendations, not followers."""
        total = self.rec_seconds + self.fol_seconds + self.shr_seconds
        return self.rec_seconds / total if total > 0 else None

    @property
    def retention(self):
        if not self.avg_seconds or not self.duration:
            return None
        return self.avg_seconds / self.duration

    @property
    def view_through(self):
        """Views per person reached — did the thumbnail/opening stop the scroll?"""
        if not self.reach or self.views is None:
            return None
        return self.views / self.reach

    def curve_at(self, fraction):
        """Retention at a fraction (0-1) through the video."""
        if not self.curve:
            return None
        idx = min(int(round(fraction * (len(self.curve) - 1))), len(self.curve) - 1)
        return self.curve[idx]


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"{path}: no rows")

    interval_cols = []
    for col in rows[0].keys():
        match = INTERVAL_RE.search(col or "")
        if match:
            interval_cols.append((int(match.group(1)), col))
    interval_cols.sort()

    required = {"3-second video views", "Seconds viewed", "Publish time"}
    missing = required - set(rows[0].keys())
    if missing:
        raise SystemExit(
            f"{path}: missing {sorted(missing)}.\nThis script expects the "
            f"*video-level* export (Content level: Video). Use fb_analyze.py for "
            f"post-level exports."
        )

    reels = [Reel(r, interval_cols) for r in rows]
    return [r for r in reels if r.views is not None and r.date]


def section_traffic(reels, out):
    out.append("## Where your views actually come from\n")
    total_rec = sum(r.rec_seconds for r in reels)
    total_fol = sum(r.fol_seconds for r in reels)
    total_shr = sum(r.shr_seconds for r in reels)
    grand = total_rec + total_fol + total_shr
    if grand <= 0:
        out.append("_No traffic-source data in this export._\n")
        return

    out.append(f"Across all {len(reels)} reels, watch time splits:\n")
    out.append("| Source | Watch time (hours) | Share |")
    out.append("|---|---:|---:|")
    for label, value in (("Recommendations", total_rec), ("Followers", total_fol),
                         ("Shares", total_shr)):
        out.append(f"| {label} | {fmt(value / 3600)} | {pct(value / grand)} |")
    out.append("")
    out.append(f"**{pct(total_rec / grand)} of your watch time is recommendation "
               f"traffic** — Facebook showing your reels to people who do not follow "
               f"you. Your {fmt(total_fol / 3600)} hours of follower watch time is "
               f"the floor you keep regardless; everything above it is the "
               f"recommendation engine deciding, reel by reel, whether to push you.\n")

    out.append("### Month by month\n")
    months = defaultdict(list)
    for r in reels:
        months[(r.date.year, r.date.month)].append(r)

    out.append("| Month | Reels | Median views | Recommendation watch (hrs) | "
               "Follower watch (hrs) | Rec. share |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for key in sorted(months):
        group = months[key]
        rec = sum(r.rec_seconds for r in group)
        fol = sum(r.fol_seconds for r in group)
        shr = sum(r.shr_seconds for r in group)
        tot = rec + fol + shr
        out.append(
            f"| {key[0]}-{key[1]:02d} | {len(group)} | "
            f"{fmt(median([r.views for r in group]))} | {fmt(rec / 3600)} | "
            f"{fmt(fol / 3600)} | {pct(rec / tot) if tot else '—'} |"
        )
    out.append("")


def section_reach(reels, out):
    out.append("## Delivery vs. stopping the scroll\n")
    out.append("Reach is how many people Facebook *showed* the reel to. Views is how "
               "many actually watched 3+ seconds. Separating them tells you whether a "
               "weak reel was under-delivered or simply not clicked into.\n")
    months = defaultdict(list)
    for r in reels:
        months[(r.date.year, r.date.month)].append(r)
    out.append("| Month | Median reach | Median views | Median view-through |")
    out.append("|---|---:|---:|---:|")
    for key in sorted(months):
        group = months[key]
        vt = [r.view_through for r in group if r.view_through is not None]
        out.append(f"| {key[0]}-{key[1]:02d} | "
                   f"{fmt(median([r.reach for r in group]))} | "
                   f"{fmt(median([r.views for r in group]))} | "
                   f"{pct(median(vt)) if vt else '—'} |")
    out.append("")


def average_curve(reels, points=11):
    """Mean retention curve across reels, sampled at even fractions."""
    samples = []
    for i in range(points):
        frac = i / (points - 1)
        vals = [r.curve_at(frac) for r in reels]
        vals = [v for v in vals if v is not None]
        samples.append(sum(vals) / len(vals) if vals else None)
    return samples


def section_curves(reels, out):
    curved = [r for r in reels if r.curve]
    if len(curved) < 10:
        return
    out.append("## Retention curves: where viewers leave\n")

    ranked = sorted(curved, key=lambda r: r.views, reverse=True)
    cut = max(3, len(ranked) // 5)
    top, bottom = ranked[:cut], ranked[-cut:]

    top_curve = average_curve(top)
    bottom_curve = average_curve(bottom)
    all_curve = average_curve(curved)

    out.append(f"Average share of viewers still watching, by position through the "
               f"reel. Top {len(top)} vs bottom {len(bottom)} by views.\n")
    out.append("| Position | Top reels | Bottom reels | All reels |")
    out.append("|---|---:|---:|---:|")
    for i, label in enumerate(["0% (start)", "10%", "20%", "30%", "40%", "50%",
                               "60%", "70%", "80%", "90%", "100% (end)"]):
        out.append(f"| {label} | {pct(top_curve[i]) if top_curve[i] else '—'} | "
                   f"{pct(bottom_curve[i]) if bottom_curve[i] else '—'} | "
                   f"{pct(all_curve[i]) if all_curve[i] else '—'} |")
    out.append("")

    # The hook window is the first ~20% and is where short-form is won or lost.
    top_hook = top_curve[2]
    bottom_hook = bottom_curve[2]
    if top_hook and bottom_hook:
        gap = top_hook - bottom_hook
        out.append(f"**Hook gap:** at 20% through, your best reels still hold "
                   f"{pct(top_hook)} of viewers; your worst hold {pct(bottom_hook)} — "
                   f"a {pct(abs(gap))} spread. ")
        if abs(gap) < 0.08:
            out.append("That gap is small, which means the hook is *not* what "
                       "separates your hits from your flops. Something upstream of "
                       "retention — topic choice — is doing the separating.\n")
        else:
            out.append("That is a wide gap: the opening seconds are doing real work "
                       "in separating hits from flops.\n")

    # End-of-reel retention is the completion signal.
    if all_curve[-1] is not None:
        out.append(f"**Completion:** on average {pct(all_curve[-1])} of viewers are "
                   f"still watching at the end.\n")


def section_duration_drift(reels, out):
    out.append("## Are you drifting longer?\n")
    months = defaultdict(list)
    for r in reels:
        if r.duration:
            months[(r.date.year, r.date.month)].append(r)
    if len(months) < 2:
        return
    out.append("| Month | Median length | Median retention | Median views |")
    out.append("|---|---:|---:|---:|")
    for key in sorted(months):
        group = months[key]
        rets = [r.retention for r in group if r.retention is not None]
        out.append(f"| {key[0]}-{key[1]:02d} | "
                   f"{fmt(median([r.duration for r in group]))}s | "
                   f"{pct(median(rets)) if rets else '—'} | "
                   f"{fmt(median([r.views for r in group]))} |")
    out.append("")


def section_length_buckets(reels, out):
    out.append("## Length vs reach, with recommendation share\n")

    def bucket(r):
        d = r.duration
        if not d:
            return None
        for limit, label in ((20, "under 20s"), (30, "20–30s"), (45, "30–45s"),
                             (60, "45–60s"), (90, "60–90s")):
            if d < limit:
                return label
        return "90s+"

    groups = defaultdict(list)
    for r in reels:
        key = bucket(r)
        if key:
            groups[key].append(r)

    out.append("| Length | Reels | Median views | Median retention | "
               "Median rec. share | Best |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for label in ["under 20s", "20–30s", "30–45s", "45–60s", "60–90s", "90s+"]:
        if label not in groups:
            continue
        group = groups[label]
        rets = [r.retention for r in group if r.retention is not None]
        recs = [r.rec_share for r in group if r.rec_share is not None]
        out.append(f"| {label} | {len(group)} | "
                   f"{fmt(median([r.views for r in group]))} | "
                   f"{pct(median(rets)) if rets else '—'} | "
                   f"{pct(median(recs)) if recs else '—'} | "
                   f"{fmt(max(r.views for r in group))} |")
    out.append("")


def section_breakouts(reels, out, threshold=20000):
    out.append("## Breakouts vs the rest\n")
    hits = [r for r in reels if r.views >= threshold]
    rest = [r for r in reels if r.views < threshold]
    if not hits or not rest:
        return
    out.append(f"Comparing the {len(hits)} reels above {fmt(threshold)} views against "
               f"the {len(rest)} below.\n")

    def summarise(label, group):
        rets = [r.retention for r in group if r.retention is not None]
        recs = [r.rec_share for r in group if r.rec_share is not None]
        vts = [r.view_through for r in group if r.view_through is not None]
        durs = [r.duration for r in group if r.duration]
        ers = [(r.reactions or 0) + (r.comments or 0) + (r.shares or 0)
               for r in group]
        er_rates = [e / r.views for e, r in zip(ers, group) if r.views]
        shr = [(r.shares or 0) / r.views for r in group if r.views]
        return (f"| {label} | {len(group)} | {fmt(median(durs))}s | "
                f"{pct(median(rets)) if rets else '—'} | "
                f"{pct(median(vts)) if vts else '—'} | "
                f"{pct(median(recs)) if recs else '—'} | "
                f"{pct(median(er_rates), 2) if er_rates else '—'} | "
                f"{pct(median(shr), 2) if shr else '—'} |")

    out.append("| Group | Reels | Median length | Retention | View-through | "
               "Rec. share | Engagement rate | Share rate |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    out.append(summarise("Breakouts", hits))
    out.append(summarise("Everything else", rest))
    out.append("")

    out.append("### The breakouts\n")
    out.append("| Views | Reach | Length | Retention | Rec. share | Title |")
    out.append("|---:|---:|---:|---:|---:|---|")
    for r in sorted(hits, key=lambda r: r.views, reverse=True):
        out.append(f"| {fmt(r.views)} | {fmt(r.reach)} | "
                   f"{fmt(r.duration)}s | {pct(r.retention) if r.retention else '—'} | "
                   f"{pct(r.rec_share) if r.rec_share else '—'} | "
                   f"{r.title[:70] or '_(no title)_'} |")
    out.append("")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="video-level CSV export")
    ap.add_argument("-o", "--out", help="write markdown here (default stdout)")
    ap.add_argument("--breakout-threshold", type=int, default=20000)
    args = ap.parse_args()

    reels = load(args.input)
    if not reels:
        raise SystemExit("no usable rows")

    out = ["# Reel distribution deep-dive",
           f"_{len(reels)} reels, "
           f"{min(r.date for r in reels):%Y-%m-%d} → "
           f"{max(r.date for r in reels):%Y-%m-%d}_\n"]

    section_traffic(reels, out)
    section_reach(reels, out)
    section_curves(reels, out)
    section_duration_drift(reels, out)
    section_length_buckets(reels, out)
    section_breakouts(reels, out, args.breakout_threshold)

    report = "\n".join(out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
