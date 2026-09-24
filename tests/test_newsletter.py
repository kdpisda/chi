"""Tests for scripts/newsletter/newsletter.py (the Sendy blog-post emailer).

Ported from kdpisda/portfolio's test_newsletter.py; adapted to getchi.dev's
/blog/<slug>/ permalinks and `description` front matter.
"""
import datetime
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "newsletter"))
import newsletter  # noqa: E402

CFG = {"base_url": "https://getchi.dev"}


def write(tmp_path, name, front, body="Body.\n"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}\n---\n\n{body}")
    return str(path)


def test_bundle_permalink_is_under_blog_and_honours_slug(tmp_path):
    p = newsletter.parse_post(write(tmp_path, "dir-name/index.md",
        "title: \"A <b> & C\"\nslug: real-slug\ndate: '2026-09-22T09:00:00+05:30'\n"
        "description: Short summary.\ndraft: false"))
    assert p["permalink"] == "/blog/real-slug/"
    assert p["date"].year == 2026
    assert p["summary"] == "Short summary."


def test_url_override_and_default_slug(tmp_path):
    p = newsletter.parse_post(write(tmp_path, "some-dir/index.md",
        "title: T\nurl: \"/custom/\"\ndate: 2026-07-15T08:00:00+02:00"))
    assert p["permalink"] == "/custom/"
    assert p["slug"] == "some-dir"


def test_drafts_unrendered_and_future_posts_are_skipped(tmp_path):
    assert newsletter.parse_post(write(tmp_path, "a/index.md", "title: T\ndraft: true")) is None
    assert newsletter.parse_post(write(tmp_path, "b/index.md",
        "title: T\nbuild:\n  render: never")) is None
    future = (datetime.datetime.now(datetime.timezone.utc)
              + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert newsletter.parse_post(write(tmp_path, "c/index.md",
        f"title: T\ndate: {future}")) is None


def test_pending_excludes_ledger_and_sorts_by_date(tmp_path):
    new = newsletter.parse_post(write(tmp_path, "n/index.md", "title: N\ndate: 2026-09-23"))
    old = newsletter.parse_post(write(tmp_path, "o/index.md", "title: O\ndate: 2026-09-01"))
    done = newsletter.parse_post(write(tmp_path, "d/index.md", "title: D\ndate: 2026-08-01"))
    pending = newsletter.pending_posts([new, done, old], {"/blog/d/"})
    assert [p["slug"] for p in pending] == ["o", "n"]


def test_html_escapes_and_links_with_utm(tmp_path):
    p = newsletter.parse_post(write(tmp_path, "x/index.md",
        "title: \"A <b> & C\"\nslug: x\ndate: 2026-09-22\ndescription: \"1 < 2\""))
    out = newsletter.build_html(CFG, p)
    assert "A &lt;b&gt; &amp; C" in out and "1 &lt; 2" in out
    assert ("https://getchi.dev/blog/x/?utm_source=sendy&amp;utm_medium=email"
            "&amp;utm_campaign=x") in out
    assert "<unsubscribe" in out
    assert "[unsubscribe]" in newsletter.build_plain(CFG, p)


def test_newsletter_front_matter_drives_the_email(tmp_path):
    p = newsletter.parse_post(write(tmp_path, "y/index.md",
        "title: Post title\nslug: y\ndate: 2026-09-22\ndescription: Meta.\n"
        "newsletter:\n  subject: Custom subject\n  preheader: Peek text\n  body: |\n"
        "    First <para>\n    wraps here.\n\n    See [the RFC](https://example.com/a?b=1&c=2) and `x<y`."))
    assert newsletter.subject(p) == "Custom subject"
    assert newsletter.paragraphs(p) == [
        "First <para> wraps here.", "See [the RFC](https://example.com/a?b=1&c=2) and `x<y`."]
    out = newsletter.build_html(CFG, p)
    assert "Peek text" in out and "Meta." not in out
    assert "First &lt;para&gt; wraps here." in out
    assert '<a href="https://example.com/a?b=1&amp;c=2" style="color:#15803d;">the RFC</a>' in out
    assert ">x&lt;y</code>" in out
    assert ("See the RFC (https://example.com/a?b=1&c=2) and `x<y`."
            in newsletter.build_plain(CFG, p))


def test_missing_newsletter_block_falls_back_to_title_and_description(tmp_path):
    p = newsletter.parse_post(write(tmp_path, "z/index.md",
        "title: Post title\nslug: z\ndate: 2026-09-22\ndescription: Meta."))
    assert newsletter.subject(p) == "Post title"
    assert newsletter.paragraphs(p) == ["Meta."]


def test_reserve_then_send_only_live_posts(tmp_path, monkeypatch):
    posts = tmp_path / "blog"
    write(posts, "live/index.md", "title: Live\nslug: live\ndate: 2026-09-22")
    write(posts, "slow/index.md", "title: Slow\nslug: slow\ndate: 2026-09-23")
    write(posts, "old/index.md", "title: Old\nslug: old\ndate: 2026-09-01")
    ledger = tmp_path / "sent.txt"
    ledger.write_text("/blog/old/\n")
    monkeypatch.setattr(newsletter, "POSTS", str(posts))
    monkeypatch.setattr(newsletter, "LEDGER", str(ledger))
    monkeypatch.setattr(newsletter, "load_config", lambda: {
        "base_url": "https://getchi.dev", "sendy_url": "https://sendy.example",
        "list_id": "L", "brand_id": "", "from_name": "chi", "from_email": "a@b.c"})
    monkeypatch.setattr(newsletter, "is_live", lambda url: not url.endswith("/slow/"))
    sent = []
    monkeypatch.setattr(newsletter, "send",
                        lambda cfg, key, post: sent.append(post["slug"])
                        or "Campaign created and now sending")
    monkeypatch.setenv("SENDY_API_KEY", "k")
    todo = tmp_path / "to-send.txt"

    assert newsletter.main(["x", "reserve", str(todo)]) == 0
    assert todo.read_text() == "/blog/live/\n"
    assert newsletter.read_ledger() == {"/blog/old/", "/blog/live/"}  # recorded before sending
    assert newsletter.main(["x", "send", str(todo)]) == 0
    assert sent == ["live"]


def test_send_with_nothing_reserved_is_a_noop(tmp_path):
    assert newsletter.main(["x", "send", str(tmp_path / "missing.txt")]) == 0


def test_configured_reflects_the_sendy_list(capsys, monkeypatch):
    base = {"base_url": "https://getchi.dev", "sendy_url": "https://sendy.example",
            "brand_id": "", "from_name": "chi", "from_email": ""}
    monkeypatch.setattr(newsletter, "load_config", lambda: {**base, "list_id": ""})
    assert newsletter.main(["x", "configured"]) == 0
    assert capsys.readouterr().out.strip() == "enabled=false"
    monkeypatch.setattr(newsletter, "load_config", lambda: {**base, "list_id": "L"})
    newsletter.main(["x", "configured"])
    assert capsys.readouterr().out.strip() == "enabled=true"


def test_repo_config_loads_and_ledger_covers_every_published_post():
    # The real site config parses, and every post present when this runs is
    # already in the ledger or one of the (few) new ones the next deploy will
    # send — guards against a wiped ledger blasting the list.
    cfg = newsletter.load_config()
    assert cfg["base_url"] == "https://getchi.dev"
    pending = newsletter.pending_posts(newsletter.load_posts(), newsletter.read_ledger())
    assert len(pending) <= 3


def test_reserve_refuses_without_a_sender_and_leaves_the_ledger_alone(tmp_path, monkeypatch):
    posts = tmp_path / "blog"
    write(posts, "new/index.md", "title: New\nslug: new\ndate: 2026-09-22")
    ledger = tmp_path / "sent.txt"
    ledger.write_text("")
    monkeypatch.setattr(newsletter, "POSTS", str(posts))
    monkeypatch.setattr(newsletter, "LEDGER", str(ledger))
    monkeypatch.setattr(newsletter, "load_config", lambda: {
        "base_url": "https://getchi.dev", "sendy_url": "https://sendy.example",
        "list_id": "L", "brand_id": "", "from_name": "chi", "from_email": ""})
    monkeypatch.setattr(newsletter, "is_live", lambda url: True)
    todo = tmp_path / "to-send.txt"
    assert newsletter.main(["x", "reserve", str(todo)]) == 1
    assert newsletter.read_ledger() == set()
    assert not todo.exists()
