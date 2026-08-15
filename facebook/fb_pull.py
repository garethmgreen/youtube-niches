#!/usr/bin/env python3
"""Pull Facebook Page reels/videos and their per-post insights via the Graph API.

Writes a single JSON file that fb_analyze.py consumes.

Usage:
    export FB_TOKEN="EAAG..."          # user token or page token
    python3 fb_pull.py --out data/page_data.json

    # if you have several pages and want to pick one explicitly:
    python3 fb_pull.py --list-pages
    python3 fb_pull.py --page-id 1234567890 --out data/page_data.json

Only the standard library is used, so this runs on any Python 3.8+.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v21.0"

# Fields that exist on the video_reels / videos edges. Requested defensively:
# if the API rejects one, we retry with the offending field removed.
REEL_FIELDS = [
    "id",
    "title",
    "description",
    "created_time",
    "updated_time",
    "length",
    "permalink_url",
    "post_id",
    "content_category",
]

POST_FIELDS = [
    "id",
    "message",
    "created_time",
    "permalink_url",
    "status_type",
    "attachments{media_type,type,title,description}",
    "shares",
    "comments.summary(true).limit(0)",
    "reactions.summary(true).limit(0)",
]


class GraphError(RuntimeError):
    def __init__(self, payload, url):
        self.payload = payload
        self.url = url
        err = (payload or {}).get("error", {})
        self.code = err.get("code")
        self.subcode = err.get("error_subcode")
        self.message = err.get("message", str(payload))
        super().__init__(f"{self.message} (code={self.code}, subcode={self.subcode})")


def graph_get(path, token, version=DEFAULT_VERSION, retries=4, **params):
    """GET a Graph API path, retrying on transient/rate-limit errors."""
    params = {k: v for k, v in params.items() if v is not None}
    params["access_token"] = token
    url = f"{GRAPH}/{version}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"

    delay = 2
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {"error": {"message": body}}
            err = GraphError(payload, url)
            # 4 = app rate limit, 17 = user rate limit, 613 = calls-per-hour,
            # 1/2 = transient platform errors. Everything else is a real failure.
            transient = err.code in (1, 2, 4, 17, 341, 613) or exc.code >= 500
            if transient and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise err from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def graph_get_tolerant(path, token, fields, version=DEFAULT_VERSION, **params):
    """Like graph_get, but drops individual fields the API rejects.

    Graph API field availability shifts between versions and permission sets;
    losing one optional field should not lose the whole pull.
    """
    remaining = list(fields)
    while True:
        try:
            return graph_get(
                path, token, version=version, fields=",".join(remaining), **params
            )
        except GraphError as err:
            dropped = None
            for field in remaining:
                name = field.split("{")[0].split(".")[0]
                if name and name in err.message and name != "id":
                    dropped = field
                    break
            if dropped is None or len(remaining) <= 1:
                raise
            remaining.remove(dropped)
            print(f"  ! dropped unsupported field '{dropped}'", file=sys.stderr)


def paginate(path, token, fields, version=DEFAULT_VERSION, limit=100, max_items=None):
    """Walk a Graph edge, yielding every node until exhausted or max_items."""
    data = graph_get_tolerant(path, token, fields, version=version, limit=limit)
    seen = 0
    while True:
        for node in data.get("data", []):
            yield node
            seen += 1
            if max_items and seen >= max_items:
                return
        nxt = (data.get("paging") or {}).get("next")
        if not nxt:
            return
        try:
            with urllib.request.urlopen(nxt, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"  ! pagination stopped: {exc}", file=sys.stderr)
            return


def list_pages(token, version=DEFAULT_VERSION):
    """Return the pages this token can act on, each with its own page token."""
    out = []
    for node in paginate(
        "me/accounts",
        token,
        ["id", "name", "access_token", "followers_count", "fan_count", "category"],
        version=version,
    ):
        out.append(node)
    return out


def fetch_video_insights(video_id, token, version=DEFAULT_VERSION):
    """Fetch every available insight metric for one video/reel.

    Deliberately omits the `metric` parameter: the API then returns whatever
    metric set it currently supports for that video type, which keeps this
    working as Meta renames reel metrics between versions.
    """
    try:
        data = graph_get(f"{video_id}/video_insights", token, version=version)
    except GraphError as err:
        return {"_error": err.message}

    metrics = {}
    for row in data.get("data", []):
        name = row.get("name")
        values = row.get("values") or []
        if not name or not values:
            continue
        value = values[0].get("value")
        if isinstance(value, dict):
            # e.g. total_video_reactions_by_type_total -> {"like": 10, "love": 3}
            metrics[name] = value
            metrics[f"{name}__sum"] = sum(
                v for v in value.values() if isinstance(v, (int, float))
            )
        else:
            metrics[name] = value
    return metrics


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.environ.get("FB_TOKEN"),
                    help="Graph API token (defaults to $FB_TOKEN)")
    ap.add_argument("--page-id", help="Page ID to pull; omit to auto-select")
    ap.add_argument("--version", default=DEFAULT_VERSION, help="Graph API version")
    ap.add_argument("--limit", type=int, default=300,
                    help="Max reels/videos to pull (default 300)")
    ap.add_argument("--skip-posts", action="store_true",
                    help="Skip the non-video posts pull")
    ap.add_argument("--list-pages", action="store_true",
                    help="List pages this token can access, then exit")
    ap.add_argument("--out", default="data/page_data.json", help="Output JSON path")
    args = ap.parse_args()

    if not args.token:
        ap.error("no token: pass --token or set FB_TOKEN")

    token = args.token
    version = args.version

    # --- resolve the page and swap in its page-scoped token ---------------
    page = None
    try:
        pages = list_pages(token, version=version)
    except GraphError as err:
        # A page-scoped token can't read me/accounts; that's fine if --page-id given.
        pages = []
        if not args.page_id:
            print(f"could not list pages: {err}", file=sys.stderr)

    if args.list_pages:
        if not pages:
            print("No pages returned. The token may be page-scoped already.")
        for p in pages:
            followers = p.get("followers_count") or p.get("fan_count") or "?"
            print(f"{p['id']}  {p.get('name','?')}  ({followers} followers)")
        return 0

    if pages:
        if args.page_id:
            page = next((p for p in pages if p["id"] == args.page_id), None)
        elif len(pages) == 1:
            page = pages[0]
        else:
            print("Multiple pages found; pass --page-id for one of:", file=sys.stderr)
            for p in pages:
                print(f"  {p['id']}  {p.get('name','?')}", file=sys.stderr)
            return 2
        if page and page.get("access_token"):
            token = page["access_token"]

    page_id = args.page_id or (page or {}).get("id")
    if not page_id:
        page_id = "me"

    # --- page profile snapshot -------------------------------------------
    profile = {}
    try:
        profile = graph_get_tolerant(
            page_id, token,
            ["id", "name", "username", "followers_count", "fan_count", "link", "category"],
            version=version,
        )
    except GraphError as err:
        print(f"page profile unavailable: {err}", file=sys.stderr)

    print(f"Pulling page {profile.get('name', page_id)} "
          f"({profile.get('followers_count', '?')} followers)", file=sys.stderr)

    # --- reels ------------------------------------------------------------
    videos = []
    seen_ids = set()
    for edge in ("video_reels", "videos"):
        try:
            nodes = list(paginate(f"{page_id}/{edge}", token, REEL_FIELDS,
                                  version=version, max_items=args.limit))
        except GraphError as err:
            print(f"  ! {edge} edge failed: {err}", file=sys.stderr)
            continue
        for node in nodes:
            if node["id"] in seen_ids:
                continue
            seen_ids.add(node["id"])
            node["_source_edge"] = edge
            videos.append(node)
        print(f"  {edge}: {len(nodes)} items", file=sys.stderr)
        if len(videos) >= args.limit:
            break

    videos = videos[: args.limit]

    # --- per-video insights ------------------------------------------------
    for i, video in enumerate(videos, 1):
        video["insights"] = fetch_video_insights(video["id"], token, version=version)
        if i % 10 == 0 or i == len(videos):
            print(f"  insights {i}/{len(videos)}", file=sys.stderr)

    # --- non-video posts (context: does the page still reach people at all?)
    posts = []
    if not args.skip_posts:
        try:
            posts = list(paginate(f"{page_id}/published_posts", token, POST_FIELDS,
                                  version=version, max_items=args.limit))
            print(f"  published_posts: {len(posts)} items", file=sys.stderr)
        except GraphError as err:
            print(f"  ! published_posts failed: {err}", file=sys.stderr)

    payload = {
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "graph_version": version,
        "page": profile,
        "videos": videos,
        "posts": posts,
    }

    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(videos)} videos and {len(posts)} posts to {out_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
