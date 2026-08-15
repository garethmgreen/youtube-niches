# Facebook Page reel analysis

Two scripts that answer "what should I be posting, and in what format" from your own
Page data rather than from guesswork.

- `fb_pull.py` — pulls your reels and their per-reel insights from the Meta Graph API.
- `fb_analyze.py` — turns that into a markdown report: when performance changed, what
  correlates with views, which lengths and topics over-perform, and what to do next.

Both are pure standard-library Python 3.8+. No installs, no dependencies.

---

## Is there a Meta connector for Claude?

No. There is no official Meta/Facebook connector in the Claude connector directory, so
a Page's organic reel data cannot be read directly from a chat session. The third-party
marketing connectors that do exist (Supermetrics, Windsor.ai) are paid, ads-focused, and
still require you to authorise Meta on their side — they are not a shortcut.

The two routes below are the real ones. Route B needs no developer account at all.

---

## Route A — Graph API (richer: retention, follows-per-reel)

This is worth the ten minutes, because it returns the fields that actually explain
distribution — average watch time and follows per reel — which the CSV export does not
always include.

1. Go to <https://developers.facebook.com/tools/explorer/> and log in with the account
   that manages the Page.
2. If you have no app yet, create one (type: **Business**). It does not need review,
   submission, or to ever go live — it exists only to mint a token for yourself.
3. In the Explorer's right-hand panel, set **Meta App** to your app, then open the
   **User or Page** dropdown and choose **Get Page Access Token**. Pick your Page.
4. Add these permissions, then click **Generate Access Token**:
   - `pages_show_list`
   - `pages_read_engagement`
   - `read_insights`
   - `pages_read_user_content`
5. Copy the token.

Then run:

```bash
export FB_TOKEN="EAAG...paste-your-token"

python3 fb_pull.py --list-pages              # confirm the Page is visible
python3 fb_pull.py --out data/page_data.json # pull reels + insights
python3 fb_analyze.py data/page_data.json -o report.md
```

Explorer tokens are short-lived (roughly an hour). That is fine — the pull takes a
minute and you only need the token while it runs. If it expires mid-pull, generate a new
one and re-run.

**Treat the token like a password.** It can read and post as your Page. Do not commit
it, paste it into a chat, or share it. `data/` is gitignored here for that reason.

## Route B — Business Suite CSV export (no developer account)

1. Open **Meta Business Suite** → **Insights** → **Content**.
2. Set the date range as wide as it allows, and filter to reels.
3. Use the export/download control to save the data as **CSV**.

```bash
python3 fb_analyze.py "Content Export.csv" -o report.md
```

Column names in these exports vary by locale and version, so the analyzer matches
headers fuzzily and prints which columns it matched. Check that line — if `views` or
`duration` matched the wrong column, the report will be wrong. If it cannot find a views
column it fails loudly and lists your actual headers rather than guessing.

---

## Reading the report

The report has eight sections. Three carry most of the weight:

**Section 2 — when it changed.** Monthly *medians*, not averages. One 500K reel drags an
average up and hides a falling floor; the median shows the floor. This is what tells you
whether a slowdown is real or just the absence of one lucky hit.

**Section 3 — what correlates with views.** Rank correlations between each signal and
views. Note that share and comment figures here are *rates*, not raw counts: a reel with
ten times the views mechanically collects about ten times the shares, so correlating raw
counts against views is circular and always produces a fake "shares drive views" result.
Rates answer the real question — do reels that get shared unusually often for their size
travel further?

**Section 6 — topic lift.** Median views of reels whose caption contains a term versus
reels without it. A term needs to appear in at least three reels (`--min-topic-posts` to
raise that) before it is listed. Small-sample rows are leads to test, not conclusions.

A caveat that applies throughout: this measures correlation on observational data. If
you happened to post your best-executed reels on one topic, that topic's lift absorbs
the execution quality too. The way to separate them is to deliberately post a weaker
idea in the winning format, and a strong idea in the losing one, and compare.

## Caveats worth knowing

- **Views are not comparable across metric names.** Meta has repeatedly changed what a
  reel "play" counts as. `fb_pull.py` deliberately requests all available metrics
  instead of naming one, and the analyzer picks in priority order, so a rename degrades
  the report rather than breaking it — but a year-long comparison may straddle a
  definition change.
- **Retention above 100% means loops**, not an error: viewers watched the reel more than
  once. It is capped at 500% so a heavily-looped short reel cannot distort the medians.
- **Deleted reels are absent**, so historical medians are survivor-biased upward if you
  have removed under-performers.
