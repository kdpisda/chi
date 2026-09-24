# chi website

The Hugo site for chi: landing page + docs. No external theme, no submodules,
no npm — a custom minimal theme lives in `layouts/` with a single stylesheet
in `assets/css/main.css`.

## Preview

From the repo root (requires [Hugo](https://gohugo.io/installation/) extended,
v0.146+):

```sh
hugo server --source website
```

Then open http://localhost:1313/.

## Build

```sh
hugo --source website --minify
```

Output lands in `website/public/`. `baseURL` in `website/hugo.toml` is a
placeholder — override it at deploy time:

```sh
hugo --source website --minify -b https://your-domain.example/
```

## Editing

- Landing page copy (hero, feature grid): front matter of `content/_index.md`;
  the terminal transcript is `layouts/partials/terminal.html`.
- Docs: `content/docs/*.md`. The sidebar order comes from the `[[menus.docs]]`
  entries in `hugo.toml`.
- Blog: `content/blog/<slug>/index.md` (see below). Listing is
  `layouts/blog/section.html`, a post is `layouts/blog/page.html`.
- Styles: `assets/css/main.css` (dark, terminal-native; system font stack +
  monospace accents).

Keep docs grounded in the code and the specs under the repo's `docs/`
directory — no invented benchmarks or features.

## Writing a blog post

```sh
hugo new content --source website blog/my-post-slug/index.md
```

That scaffolds the front matter from `archetypes/blog.md` with `draft: true`.
Drafts render under `hugo server -D` and are never published. Flip `draft` to
`false` when it's ready, and push to `main` to deploy.

Front matter that matters:

| Field | Used for |
|---|---|
| `title` | `<h1>`, `<title>`, social card, RSS |
| `description` | meta description, social card text, listing teaser, RSS — 140–160 chars |
| `date` | ordering and `datePublished`. **A future date is not published** until a build runs after it, and deploys only run on push, so a scheduled post needs a later push or a manual `workflow_dispatch` |
| `category` | label on cards and `article:section` — Guide · Deep dive · Comparison · Release · Case study |
| `tags` | `article:tag` + JSON-LD keywords (no tag pages are built) |
| `toc` | `true` adds a sticky "on this page" sidebar; worth it for 4+ sections |
| `image` | optional 1200×630 social card; a file next to `index.md` or a site path. Defaults to `/og.png` |
| `author` | defaults to `params.author` |
| `newsletter` | `subject` / `preheader` / `body` of the subscriber email (see below). Without it the email falls back to title + `description` |

Every post gets, from the template: a "try it" install box, share links, prev/next,
three more posts, `BlogPosting` + breadcrumb JSON-LD, `og:type=article`, and an
entry in the sitemap and in `/blog/index.xml`. The three newest posts also show
on the landing page. Add each new post to `static/llms.txt`.

Hold the blog to the same bar as the docs: every number and claim should trace
to the code, a run you actually did, or a cited source. For comparison posts,
check claims against the other project's current README, date the comparison,
and say where the other tool is ahead.

## Newsletter (Sendy)

Every new blog post is emailed to a Sendy list automatically. It's the same
setup as kdpisda.in (`kdpisda/portfolio`) and jeenotes, on the same self-hosted
Sendy instance.

- `.github/workflows/newsletter.yml` runs after each successful `deploy-pages`
  run on `main`. `scripts/newsletter/newsletter.py` creates and sends one Sendy
  campaign for each published post whose permalink isn't yet in
  `scripts/newsletter/sent.txt`.
- Delivery is at-most-once. The post has to return 200 on getchi.dev first.
  Then it's added to the ledger, and the ledger is pushed to `main` *before* the
  email is sent. A failure can skip an email (the job fails and names the post)
  but can never send one twice. The ledger lives outside `website/`, so the
  ledger commit doesn't trigger a redeploy.
- The email body comes from the post's `newsletter:` front matter (see the
  archetype). The layout is `scripts/newsletter/email-template.html`.
- **Preview / dry run:** `python3 scripts/newsletter/newsletter.py` lists what
  the next deploy would send and writes `scripts/newsletter/preview-email.html`
  (gitignored).
- **Guard rails:** if more than 3 posts are pending it refuses to send, so a
  wiped ledger can't spam the list. Raise the limit with the `max_posts` input
  on a manual run, or run `newsletter.py seed` to mark every current post as
  sent. Future-dated and draft posts are never pending.
- **Re-send a post:** delete its line from `sent.txt`, push, then run the
  workflow manually.
- The launch posts are already in `sent.txt`, so the first email goes out for
  the *next* post.

**Setup (owner, one time). The newsletter is off until this is done:**

1. In Sendy, create a list for getchi.dev (under the brand you want). Copy its
   list ID from the list's **embed form** code (the `list` field), *not* the
   hosted-page `?f=` token.
2. In `website/hugo.toml` `[params]`, set `sendyList` to that ID and
   `newsletterFrom` to a sender address verified in SES for that brand. That
   turns on the subscribe form (blog listing, every post, landing page) and the
   workflow.
3. Add the repo secret `SENDY_API_KEY`. Optional repo variables:
   `SENDY_BRAND_ID`, `NEWSLETTER_FROM_NAME` (default "chi (getchi.dev)") and
   `NEWSLETTER_FROM_EMAIL` (overrides `newsletterFrom`).

The site doesn't have a privacy page yet. Once it collects emails, it should
say what's stored (name/email in Sendy on marketing.happychases.com) and how
to unsubscribe. kdpisda.in's `content/legal/privacy.md` is a good model.

