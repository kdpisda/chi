#!/usr/bin/env python3
"""Email newsletter subscribers about newly published getchi.dev blog posts via Sendy.

Ported from kdpisda/portfolio (scripts/newsletter/newsletter.py), which mirrors
the jeenotes Sendy flow: one Sendy campaign per new post, created and sent
through the campaigns/create.php API. "New" means the post's permalink is not
yet in sent.txt, a committed ledger, so reruns never double-send.

Each post carries its own email in front matter:

  newsletter:
    subject: "Inbox subject line"
    preheader: "Grey preview text after the subject"
    body: |
      First paragraph. [Links](https://example.com) are allowed.

      Second paragraph.

Posts without it fall back to the title + description (with a warning).

  python3 scripts/newsletter/newsletter.py                 # dry run: list pending, write preview
  python3 scripts/newsletter/newsletter.py reserve FILE    # add live pending posts to the ledger + FILE
  python3 scripts/newsletter/newsletter.py send FILE       # send the posts listed in FILE
  python3 scripts/newsletter/newsletter.py seed            # mark every published post as sent
  python3 scripts/newsletter/newsletter.py configured      # print enabled=true|false (for the workflow)

The workflow commits and pushes the ledger between `reserve` and `send`, so an
email goes out only once its ledger entry is durable on main.

Run by .github/workflows/newsletter.yml after each successful deploy-pages run.
Sendy URL and list ID come from website/hugo.toml [params] sendyUrl/sendyList
(the same list the site's subscribe form uses), the from-address from
params.newsletterFrom. An empty sendyList means the newsletter is off: the form
isn't rendered and the workflow skips. The email layout lives in
email-template.html. Env vars SENDY_API_KEY (required for send), SENDY_BRAND_ID,
NEWSLETTER_FROM_NAME, NEWSLETTER_FROM_EMAIL and NEWSLETTER_MAX_POSTS override
or extend them.
"""
import datetime
import html
import os
import re
import string
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SITE = os.path.join(REPO, "website")
POSTS = os.path.join(SITE, "content", "blog")
SECTION = "blog"
LEDGER = os.path.join(HERE, "sent.txt")
PREVIEW = os.path.join(HERE, "preview-email.html")
TEMPLATE = os.path.join(HERE, "email-template.html")

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def load_config():
    with open(os.path.join(SITE, "hugo.toml"), "rb") as f:
        site = tomllib.load(f)
    params = site.get("params", {})
    return {
        "base_url": site["baseURL"].rstrip("/"),
        "sendy_url": str(params.get("sendyUrl") or "").rstrip("/"),
        "list_id": str(params.get("sendyList") or ""),
        "brand_id": os.environ.get("SENDY_BRAND_ID", ""),
        "from_name": os.environ.get("NEWSLETTER_FROM_NAME") or "chi (getchi.dev)",
        "from_email": os.environ.get("NEWSLETTER_FROM_EMAIL") or params.get("newsletterFrom", ""),
    }


def is_configured(cfg):
    return bool(cfg["sendy_url"] and cfg["list_id"])


def parse_post(path):
    """Return a post dict from a markdown file, or None if it isn't published."""
    m = FRONT_RE.match(open(path, encoding="utf-8").read())
    if not m:
        return None
    fm = yaml.safe_load(m.group(1)) or {}
    build = fm.get("build") or {}
    if fm.get("draft") or build.get("render") == "never":
        return None
    if os.path.basename(path) == "index.md":
        default_slug = os.path.basename(os.path.dirname(path))
    else:
        default_slug = os.path.splitext(os.path.basename(path))[0]
    slug = fm.get("slug") or default_slug
    permalink = fm.get("url") or f"/{SECTION}/{slug}/"
    date = fm.get("date")
    if isinstance(date, str):
        date = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
    elif isinstance(date, datetime.date) and not isinstance(date, datetime.datetime):
        date = datetime.datetime.combine(date, datetime.time())
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=datetime.timezone.utc)
    if date is not None and date > datetime.datetime.now(datetime.timezone.utc):
        return None  # Hugo doesn't publish future-dated posts; neither do we
    return {
        "title": str(fm["title"]),
        "slug": slug,
        "permalink": permalink,
        "date": date,
        "summary": str(fm.get("description") or ""),
        "tags": fm.get("tags") or [],
        "email": parse_email(fm.get("newsletter")),
    }


def parse_email(block):
    block = block if isinstance(block, dict) else {}
    body = str(block.get("body") or "").strip()
    return {
        "subject": str(block.get("subject") or "").strip(),
        "preheader": str(block.get("preheader") or "").strip(),
        "paragraphs": [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()],
    }


def load_posts():
    posts = []
    for entry in sorted(os.listdir(POSTS)):
        full = os.path.join(POSTS, entry)
        if os.path.isdir(full):
            full = os.path.join(full, "index.md")
        elif not entry.endswith(".md") or entry.startswith("_"):
            continue
        if os.path.exists(full):
            post = parse_post(full)
            if post:
                posts.append(post)
    return posts


def read_ledger():
    if not os.path.exists(LEDGER):
        return set()
    return {line.strip() for line in open(LEDGER) if line.strip() and not line.startswith("#")}


def write_ledger(sent):
    with open(LEDGER, "w") as f:
        f.write("# Permalinks already emailed to newsletter subscribers (one per line).\n"
                "# Managed by scripts/newsletter/newsletter.py; remove a line to re-send.\n")
        f.writelines(f"{p}\n" for p in sorted(sent))


def pending_posts(posts, sent):
    fresh = [p for p in posts if p["permalink"] not in sent]
    return sorted(fresh, key=lambda p: p["date"].timestamp() if p["date"] else 0)


def post_url(cfg, post, utm=True):
    url = cfg["base_url"] + post["permalink"]
    if utm:
        url += "?" + urllib.parse.urlencode({
            "utm_source": "sendy", "utm_medium": "email", "utm_campaign": post["slug"]})
    return url


def pretty_date(post):
    d = post["date"]
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}" if d else ""


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def subject(post):
    return post["email"]["subject"] or post["title"]


def preheader(post):
    return post["email"]["preheader"] or post["summary"][:180]


def paragraphs(post):
    return post["email"]["paragraphs"] or ([post["summary"]] if post["summary"] else [])


CODE_RE = re.compile(r"`([^`]+)`")


def inline_html(text):
    return CODE_RE.sub(r'<code style="font-family:Menlo,Consolas,monospace;font-size:14px;'
                       r'background:#eef2f0;padding:1px 4px;border-radius:4px;">\1</code>',
                       html.escape(text))


def para_html(text):
    """Escape a plain-text paragraph; [text](https://url) becomes a link, `x` inline code."""
    out, last = [], 0
    for m in LINK_RE.finditer(text):
        out.append(inline_html(text[last:m.start()]))
        out.append(f'<a href="{html.escape(m.group(2))}" style="color:#15803d;">'
                   f'{inline_html(m.group(1))}</a>')
        last = m.end()
    out.append(inline_html(text[last:]))
    return "".join(out)


def build_html(cfg, post):
    e = html.escape
    body_html = "\n    ".join(
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.65;color:#1f2937;">{para_html(p)}</p>'
        for p in paragraphs(post))
    body = open(TEMPLATE, encoding="utf-8").read()
    body = body[body.index("<!DOCTYPE"):]  # drop the maintainer comment
    return string.Template(body).substitute(
        title=e(post["title"]), date=e(pretty_date(post)),
        preheader=e(preheader(post)), post_url=e(post_url(cfg, post)),
        home_url=e(cfg["base_url"] + "/"), body_html=body_html)


def build_plain(cfg, post):
    parts = ["Hey [Name,fallback=there],", f"New on the chi blog: {post['title']}"]
    parts += [LINK_RE.sub(r"\1 (\2)", p) for p in paragraphs(post)]
    parts += [f"Read it: {post_url(cfg, post)}", "Unsubscribe: [unsubscribe]"]
    return "\n\n".join(parts)


def is_live(url, attempts=10, wait=30):
    """Pages can lag a deploy by a minute or two; don't email a 404."""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "getchi-newsletter"})
            if urllib.request.urlopen(req, timeout=20).status == 200:
                return True
        except urllib.error.URLError:
            pass
        if i < attempts - 1:
            time.sleep(wait)
    return False


def send(cfg, api_key, post):
    fields = {
        "api_key": api_key, "list_ids": cfg["list_id"],
        "from_name": cfg["from_name"], "from_email": cfg["from_email"],
        "reply_to": cfg["from_email"],
        "title": f"chi blog — {post['slug']}",
        "subject": subject(post),
        "plain_text": build_plain(cfg, post),
        "html_text": build_html(cfg, post),
        "send_campaign": "1",
        "track_opens": "1", "track_clicks": "1",
    }
    if cfg["brand_id"]:
        fields["brand_id"] = cfg["brand_id"]
    req = urllib.request.Request(
        f"{cfg['sendy_url']}/api/campaigns/create.php",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"User-Agent": "curl/8.4.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode().strip()


def main(argv):
    mode = argv[1] if len(argv) > 1 else "dry-run"
    if mode not in ("dry-run", "reserve", "send", "seed", "configured") or (
            mode in ("reserve", "send") and len(argv) < 3):
        sys.exit("usage: newsletter.py [seed | configured | reserve LIST_FILE | send LIST_FILE]")
    cfg = load_config()

    if mode == "configured":
        print(f"enabled={'true' if is_configured(cfg) else 'false'}")
        return 0
    posts = load_posts()
    sent = read_ledger()

    if mode == "seed":
        write_ledger(sent | {p["permalink"] for p in posts})
        print(f"ledger seeded with {len(posts)} posts")
        return 0

    if mode == "send":
        return send_reserved(cfg, posts, argv[2])

    pending = pending_posts(posts, sent)
    if not pending:
        print("No new posts to email.")
        return 0
    if not is_configured(cfg):
        print("::notice::Newsletter is off: set params.sendyList in website/hugo.toml.")
    print(f"Sendy list {cfg['list_id'] or '(unset)'} at {cfg['sendy_url'] or '(unset)'}, "
          f"from {cfg['from_email'] or '(unset)'}")
    for p in pending:
        print(f"pending: {p['permalink']} | subject: {subject(p)}")
        if not p["email"]["paragraphs"]:
            print(f"::warning::{p['permalink']} has no `newsletter:` front matter; "
                  "the email falls back to its title and description")

    # A wiped or broken ledger would otherwise blast every post to the list.
    limit = int(os.environ.get("NEWSLETTER_MAX_POSTS") or 3)
    if len(pending) > limit:
        print(f"::error::{len(pending)} unsent posts exceeds NEWSLETTER_MAX_POSTS={limit}. "
              "If that's intended, rerun with a higher limit; otherwise run "
              "`newsletter.py seed` to mark them as sent.")
        return 1

    if mode == "dry-run":
        with open(PREVIEW, "w") as f:
            f.write(build_html(cfg, pending[-1]))
        print(f"\npreview written: {os.path.relpath(PREVIEW, REPO)} (dry run - nothing sent)")
        return 0

    # A post reserved without a way to send it would be marked as emailed and
    # silently skipped, so check the sender before touching the ledger.
    if not is_configured(cfg) or not cfg["from_email"]:
        print("::error::No sender address: set params.newsletterFrom in website/hugo.toml "
              "or the NEWSLETTER_FROM_EMAIL repo variable. Nothing was reserved or sent.")
        return 1

    # reserve: record live posts in the ledger *before* sending. The workflow
    # pushes the ledger, and only then runs `send`, so a failed push can never
    # lead to a re-send on the next deploy (at-most-once delivery).
    reserved = []
    for p in pending:
        if is_live(post_url(cfg, p, utm=False)):
            reserved.append(p["permalink"])
        else:
            print(f"::warning::{p['permalink']} is not live yet; will retry on the next run")
    write_ledger(sent | set(reserved))
    with open(argv[2], "w") as f:
        f.writelines(f"{r}\n" for r in reserved)
    print(f"reserved {len(reserved)} post(s) in the ledger")
    return 0


def send_reserved(cfg, posts, list_file):
    if not os.path.exists(list_file):  # reserve found nothing new
        print("Nothing reserved; no emails to send.")
        return 0
    wanted = [line.strip() for line in open(list_file) if line.strip()]
    by_link = {p["permalink"]: p for p in posts}
    api_key = os.environ.get("SENDY_API_KEY") or sys.exit("set SENDY_API_KEY")
    if not is_configured(cfg) or not cfg["from_email"]:
        sys.exit("set params.sendyList and params.newsletterFrom (or NEWSLETTER_FROM_EMAIL)")
    failed = False
    for link in wanted:
        resp = send(cfg, api_key, by_link[link])
        if resp.startswith("Campaign created"):
            print(f"sent: {link} ({resp})")
        else:
            failed = True
            print(f"::error::Sendy rejected {link}: {resp}. It is already in sent.txt, so "
                  "it won't retry on its own: fix the cause, delete its line from "
                  "scripts/newsletter/sent.txt and re-run the Newsletter workflow.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
