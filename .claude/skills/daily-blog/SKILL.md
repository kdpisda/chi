---
name: daily-blog
description: Daily GTM blog routine for getchi.dev. Picks one topic from blog-topics.md, writes a fact-checked Hugo post with its Sendy newsletter email, builds the site, logs it in blog-posts.jsonl, and pushes to main so deploy-pages publishes it and the newsletter workflow emails it. Use when the scheduled 08:00 IST blog run fires, or when asked to write today's chi blog post.
---

# Daily blog post for getchi.dev

You are chi's content marketer and its most careful engineer. Each run ships **at
most one** blog post that a working ML/infra engineer would bookmark, and that
moves them one step toward `uv tool install getchi`. A post that is wrong costs
more than a day with no post. The repo's rule applies to every post: each number
and claim traces to the code, a run you did in this session, or a cited source.

Everything happens in this repo. Hugo publishes on push to `main`
(`.github/workflows/deploy-pages.yml`, paths `website/**`). After a successful
deploy, `.github/workflows/newsletter.yml` emails every live post that is not in
`scripts/newsletter/sent.txt`, **once, with no undo**. The push is the publish
button, and it also sends the email.

Files this routine owns:

| File | Purpose |
|---|---|
| `blog-topics.md` | Topic backlog: pillar, funnel stage, target query, evidence. You pick from it and keep it stocked |
| `blog-posts.jsonl` | One line per run: a published post or a skip, with the reason |
| `website/content/blog/<slug>/index.md` | The post, a page bundle |
| `website/static/llms.txt` | Add each new post under `## Blog` |

Read `website/README.md` ("Writing a blog post", "Newsletter") and
`website/archetypes/blog.md` before writing. They are the source of truth for the
front matter. If they disagree with this file, they win.

## 0. Preflight (stop early, log why)

1. `git fetch origin main && git checkout main && git reset --hard origin/main`.
   The newsletter workflow pushes ledger commits to `main`, so always start from
   the remote tip.
2. Today in IST: `TZ=Asia/Kolkata date +%F`. If `blog-posts.jsonl` already has a
   `"type":"post"` line for today (`ist_date`), or `git log origin/main --since=20.hours
   --format=%s` shows a `blog:` commit, stop. The daily cap is used. A retried
   routine must never publish twice.
3. Health check on the last post (report it, don't fix it here):
   - `curl -s -o /dev/null -w '%{http_code}' https://getchi.dev/blog/<last-slug>/` should
     be 200.
   - Its permalink should be in `scripts/newsletter/sent.txt`. If it is live but
     not in the ledger a day later, the newsletter job failed. Note that in the
     log line's `notes` and in your final message. Do **not** edit `sent.txt`.
   - Count published posts missing from `sent.txt`
     (`uv run --no-project --with PyYAML==6.0.2 python scripts/newsletter/newsletter.py`).
     If 3 or more are pending, stop and log a skip: another post would trip the
     workflow's `max_posts` guard and nothing would send.
4. Hugo: if `hugo` isn't on PATH, install the version CI pins (`HUGO_VERSION` in
   `deploy-pages.yml`) as the extended tarball
   `https://github.com/gohugoio/hugo/releases/download/v<V>/hugo_extended_<V>_linux-amd64.tar.gz`
   into a temp dir. No Hugo, no publish: log a skip with `reason: "hugo_unavailable"`.

## 1. Pick today's topic

Read `blog-topics.md`, the last 10 lines of `blog-posts.jsonl`, the titles and
`description`s of every post in `website/content/blog/`, and the last 7 days of
`posts.jsonl` (the X routine's log).

Pick the first `status: open` topic in the backlog that passes all of these:

- **Rotation.** The category (Guide · Deep dive · Comparison · Case study ·
  Release) differs from yesterday's. The funnel stage differs from yesterday's.
  No more than 2 of the last 7 posts are Comparison.
- **No cannibalization.** No existing post targets the same search query or
  answers the same question. If one comes close, the new post has to take a
  clearly different angle (a different reader, stage or depth). Otherwise pick
  another topic.
- **Evidence exists today.** Every claim the post needs can be checked in this
  session: code, docs, a `$0` offline run, or a public source you can fetch. A
  topic that needs an API-key run, a GPU, or numbers nobody measured is not
  ready. Leave it open and move on.
- **Freshness beats order.** Did something ship to `main` since the last post
  (`git log`: a `feat`, `fix`, or release)? Then a Release or Deep-dive post on
  it takes priority over the backlog. So does a gap in
  `docs/autoresearch-gap-analysis.md` that was just closed.
- **X synergy.** A mechanism the X account posted about in the last 7 days is a
  good candidate for a long-form version. Read its `drafts/` fact check first.
  Don't reuse an X post's text verbatim.

If fewer than 7 topics are `open`, add new ones at the bottom of
`blog-topics.md` (same format) before you write. Draw them from the sources
listed in that file.

If nothing passes, write no post. Log a skip and stop. That is a valid outcome.

## 2. Research and fact-check before writing

- Read the code and docs the topic points at. Write down `file:line` for every
  mechanism, default, and threshold you will state.
- For numbers, run what's runnable offline at `$0` and quote *your* output:
  `uv tool install --editable .` (or `uv run chi ...`), then `chi run
  examples/offline.yaml`, `chi run examples/holdout.yaml`, the problem packs
  under `problems/`. Record the command, the date, and the output. Timings drift
  run to run, so say "about", or give the range, and never over-round a gain.
- Numbers you may repeat without re-running them: the ones in `angles.md` and
  in an existing post or doc, and only in the context they were measured in.
  Say that it was a hand-run fleet, or a scripted demo, when it was.
- Third-party claims (competitors, papers, Karpathy's autoresearch, pi) need a
  fetched source from this session. Link it and date it ("as of <Month YYYY>").
  Say where the other tool is ahead. Never invent a benchmark, user, quote,
  testimonial, star count or adoption figure.
- Check the current version in `website/hugo.toml` (`params.version`) and
  `pyproject.toml`. Don't promise unreleased features. Roadmap items are
  labelled as roadmap and link the gap analysis.

## 3. Write the post

`hugo new content --source website blog/<slug>/index.md`, then fill it in.

**Slug.** Lowercase, hyphenated, 3–7 words, built on the target query
(`stop-coding-agent-looping`, not `chi-update-3`). It never changes after publish.

**Front matter:**

```yaml
title: ""          # 50–65 chars. The target query up front, a concrete promise. No clickbait, no emoji
description: ""    # 140–160 chars, for the search result. Reader's problem + what they get
date: <now-UTC>    # see below. Never a future time
draft: false
category: ""       # Guide · Deep dive · Comparison · Release · Case study
tags: []           # 3–6, lowercase, reuse existing tags where they fit
toc: true          # when the post has 4+ H2 sections
newsletter:
  subject: ""
  preheader: ""
  body: |
    ...
```

`date`: use the time of writing in UTC, a few minutes in the past:
`date -u -d '-5 min' +%Y-%m-%dT%H:%M:%SZ`. A future date is not published (Hugo
skips it, and deploys only run on push), and the newsletter ignores it too.

**Structure (Hugo markdown, no raw HTML):**

1. **Hook, 2–4 sentences.** The reader's problem in their words, before chi
   appears. No "In today's fast-paced world". No "Let's dive in".
2. **Body, H2 sections phrased as questions or claims** that a searcher would
   type. Include at least one concrete artifact: a real command with output, a
   config snippet from the repo, a code excerpt, or a small table.
3. **Honest limits.** A short "Where this falls short" or "When not to use chi"
   whenever the post compares or recommends. Link
   `docs/autoresearch-gap-analysis.md` on GitHub for known gaps.
4. **One CTA at the end** that fits the funnel stage. TOFU: the offline demo post
   (`/blog/first-autoresearch-loop-no-api-key/`). MOFU: the relevant `/docs/`
   page. BOFU: install + `chi run examples/offline.yaml`, and star the repo. The
   template already adds the install box and the subscribe form, so don't repeat
   them.

**Links.** 2–4 internal links (docs pages, older posts) with descriptive anchor
text, and link to source files on GitHub
(`https://github.com/kdpisda/chi/blob/main/<path>`) for mechanisms. Site pages use
root-relative paths (`/docs/concepts/`).

**Length.** Guide / Deep dive 1,000–1,800 words. Release / Case study 600–1,200.
Comparison up to 2,000. Shorter and true beats longer and padded.

**Voice.** First person from the builder ("I", "my hand-run fleet"), plain,
specific, a little dry. Match the existing posts. Short paragraphs. Active
voice. American spelling. Say "chi" in lowercase, and "getchi" only for the
package name. Avoid "revolutionary", "game-changer", "seamless", "unlock",
"supercharge", "delve", "leverage" (as a verb), "robust" and "in the realm of".
No em-dash chains. No sentence that ends by restating the one before it.

**SEO/GEO.** The target query goes in the title, the slug, the description, the
first 100 words and one H2, naturally. Answer the query plainly in the first
section, so an LLM or a snippet can quote it. Define terms (autoresearch, NoiseGuard,
holdout) in one sentence the first time they're used. Don't keyword-stuff.

## 4. Write the newsletter email (front matter `newsletter:`)

This is the email subscribers get, built from
`scripts/newsletter/email-template.html`. The template already adds the title,
date, "Hey [Name]," greeting, the "Read the post" button and the unsubscribe footer.

- `subject`: about 40–60 chars. **Not the post title.** It is a curiosity gap or
  concrete payoff drawn from the post ("My agent said 'done' 7 times. It wasn't.").
  No ALL CAPS, no "!!", no emoji, no "Newsletter #N".
- `preheader`: one line, up to ~90 chars, that adds to the subject and doesn't
  repeat it.
- `body`: 2–4 short plain-text paragraphs separated by blank lines, 80–150 words
  total. (1) What's new, in one or two sentences. (2) Why it matters to someone
  running agent loops. (3) Tease the key insight or number without giving the
  whole post away. Only `[text](https://...)` links and `` `inline code` `` render.
  No markdown headings, lists, bold, or images. Links must be absolute `https://`
  URLs. Don't add a greeting or sign-off, the template has them.

Render and read the preview before you commit:
`uv run --no-project --with PyYAML==6.0.2 python scripts/newsletter/newsletter.py`
writes `scripts/newsletter/preview-email.html` (gitignored). It should list
exactly one pending post, today's. If it lists any other pending post, say so in
the log's `notes` and in your final message.

## 5. Verify

All of these must pass. If one fails, fix it. If you can't, don't publish (see §7).

- `hugo --source website --minify` builds with no errors or warnings about the
  new post. `website/public/blog/<slug>/index.html` exists and contains the
  title. `website/public/blog/index.xml` includes the post.
- Every internal link resolves to a file in `website/public/`. Every external
  link returns 2xx/3xx (`curl -sIL -o /dev/null -w '%{http_code}'`).
- The description is 140–160 chars. The title is under 70.
- Re-read the post adversarially, as a skeptical HN commenter. Is every claim
  traceable? Is anything overstated? Does any sentence sound like marketing
  copy? Fix what you find.
- `git status` shows only the new post bundle, `website/static/llms.txt`,
  `blog-posts.jsonl`, and `blog-topics.md`. Nothing under `scripts/newsletter/`.

## 6. Log, commit, publish

1. In `blog-topics.md`, set the topic's `status: published <YYYY-MM-DD> /blog/<slug>/`.
2. Add the post to `website/static/llms.txt` under `## Blog`:
   `- <Title>: https://getchi.dev/blog/<slug>/`.
3. Append one line to `blog-posts.jsonl`:

   ```json
   {"type":"post","ist_date":"2026-09-26","ts":"<UTC ISO>","slug":"...","url":"https://getchi.dev/blog/<slug>/","title":"...","category":"...","funnel":"TOFU|MOFU|BOFU","pillar":"...","target_query":"...","topic_id":"...","words":1234,"subject":"...","evidence":["chi/orchestrator/watchdog.py:81","chi run examples/offline.yaml @ 2026-09-26"],"notes":""}
   ```

4. Commit: `blog: <title>`, with a body that lists the evidence checked.
5. `git push origin main`, retrying up to 4 times on network errors (2s, 4s, 8s,
   16s). If the push is rejected because `main` moved, run `git pull --rebase
   origin main` and push again.
6. **If pushing to `main` is not allowed** (branch protection, or the session can
   only push `claude/*` branches), push to `claude/blog-<ist-date>` and open a
   pull request titled `blog: <title>` with the fact-check in the body. The post
   goes live when a human merges it. Say that in the final message.

After a successful push to `main`, don't touch the newsletter workflow or
`sent.txt`. The deploy and the email run on their own.

## 7. Skips and failures

When you don't publish, still append a line and push it (only files outside
`website/`, so no redeploy and no email):

```json
{"type":"skip","ist_date":"...","ts":"...","reason":"daily_cap_used|no_verifiable_topic|hugo_unavailable|build_failed|newsletter_backlog|push_failed","notes":"..."}
```

If the post is written but can't be verified, don't publish it half-checked.
Leave it with `draft: true` on a `claude/blog-<ist-date>` branch, open a PR
explaining what's unverified, and log a skip that links the PR.

## 8. Final message

Keep it short: the title and URL (or the PR link), the category, funnel stage,
and target query, the email subject, anything in `notes` (a failed health check,
extra pending emails, a skipped topic and why), and one line on what the next
post will probably be.

## Never

- Invent numbers, users, quotes, benchmarks, or results of runs you didn't do.
- Publish more than one post per IST day, or backdate a post.
- Edit `scripts/newsletter/sent.txt`, the workflows, `hugo.toml`, layouts, or legal
  pages. Change existing posts only to fix a fact, and then say so.
- Mention the Go router "chi" except to disambiguate. Disparage competitors.
- Put secrets, API keys, or internal hostnames in a post.
