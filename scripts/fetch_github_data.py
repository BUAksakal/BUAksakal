"""Refresh data/contributions.json and data/repos.json from GitHub.

Runs daily in .github/workflows/update-readme-cards.yml. No token needed for the
contribution calendar (it is the same public HTML the profile page loads); GITHUB_TOKEN,
when set, only raises the REST rate limit for the repo list.

If GitHub can't be reached or the page format changes, the old data files are kept
so the cards still render.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
USER = os.environ.get("GH_USER", "BUAksakal")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
UA = {"User-Agent": f"{USER}-readme-cards"}


def get(url: str, headers: dict | None = None, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def attrs(tag: str) -> dict:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def scrape_calendar() -> list[dict]:
    page = get(f"https://github.com/users/{USER}/contributions").decode()
    # Tooltips carry the exact count: "No contributions on May 2nd." / "1 contribution on ..."
    tips = {}
    for tid, text in re.findall(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>(.*?)</tool-tip>', page, re.S):
        text = html.unescape(text).strip()
        m = re.match(r"(\d[\d,]*) contributions?", text)
        tips[tid] = int(m.group(1).replace(",", "")) if m else 0
    days = []
    for tag in re.findall(r"<td\b[^>]*\bdata-date=\"[^\"]+\"[^>]*>", page):
        a = attrs(tag)
        count = tips.get(a.get("id", ""))
        if count is None:  # older markup: count lived on the cell
            count = int(a.get("data-count", 0))
        days.append({"date": a["data-date"], "count": count, "level": int(a.get("data-level", 0))})
    if len(days) < 300:
        raise ValueError(f"only {len(days)} days parsed — markup changed?")
    days.sort(key=lambda d: d["date"])
    # A count of 0 on a coloured cell means the tooltip wasn't matched: trust the level instead.
    if sum(d["count"] for d in days) == 0 and any(d["level"] for d in days):
        raise ValueError("tooltips not matched")
    return days


def graphql_calendar() -> list[dict]:
    if not TOKEN:
        raise ValueError("no token for GraphQL fallback")
    q = """query($u:String!){user(login:$u){contributionsCollection{contributionCalendar{
           weeks{contributionDays{date contributionCount}}}}}}"""
    body = json.dumps({"query": q, "variables": {"u": USER}}).encode()
    res = json.loads(get("https://api.github.com/graphql",
                         {"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"}, body))
    weeks = res["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [{"date": d["date"], "count": d["contributionCount"]} for w in weeks for d in w["contributionDays"]]


def fetch_repos() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    user = json.loads(get(f"https://api.github.com/users/{USER}", h))
    repos = json.loads(get(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner", h))
    return {
        "public_repos": user["public_repos"],
        "repos": {
            r["name"]: {
                "description": r["description"] or "",
                "language": r["language"] or "",
                "stars": r["stargazers_count"],
                "forks": r["forks_count"],
                "pushed_at": r["pushed_at"],
                "fork": r["fork"],
            }
            for r in repos
        },
    }


def main() -> int:
    DATA.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ok = True
    for name, fn in (("scrape", scrape_calendar), ("graphql", graphql_calendar)):
        try:
            days = fn()
            out = {"user": USER, "source": name, "fetched_at": now,
                   "total": sum(d["count"] for d in days), "days": days}
            (DATA / "contributions.json").write_text(json.dumps(out, indent=1))
            print(f"contributions: {out['total']} over {len(days)} days via {name}")
            break
        except Exception as e:  # noqa: BLE001 — keep yesterday's data rather than fail the run
            print(f"contributions via {name} failed: {e}", file=sys.stderr)
    else:
        ok = False
    try:
        repos = fetch_repos()
        (DATA / "repos.json").write_text(json.dumps(repos, indent=1))
        print(f"repos: {repos['public_repos']} public")
    except Exception as e:  # noqa: BLE001
        print(f"repos failed, keeping old data/repos.json: {e}", file=sys.stderr)
        ok = False
    if not ok:
        print("::warning::Some GitHub data could not be refreshed; rendering from the last saved data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
