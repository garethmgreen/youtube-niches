# youtube-niches

Automated faceless-YouTube niche research using Claude Code + the NexLev MCP
(as in Leo Grundström's "automate niche research" video).

## Prompts that work

- **Find niches:** "Find 30 faceless niches with <200k subs, created in the last
  90 days, at least one video over 400k views, still uploading weekly. Table with links."
- **Competitor analysis:** "Do competitor analysis on niche #N in
  `results/<file>.xlsx` — top 10 similar channels, preferably recent, as a new sheet."

## Results

| Date | File | Criteria |
|---|---|---|
| 2026-10-15 | `results/niches-2026-10-15.xlsx` / `.csv` | faceless, <200k subs, created after 2026-07-17, ≥1 upload/week, uploaded since 2026-10-01, ≥400k monthly views, ≥1 recent video ≥400k views; top 30 by est. monthly revenue |

Revenue figures are NexLev AdSense estimates, not verified earnings.
