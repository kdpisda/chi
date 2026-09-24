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

Every post gets, from the template: a "try it" install box, share links, prev/next,
three more posts, `BlogPosting` + breadcrumb JSON-LD, `og:type=article`, and an
entry in the sitemap and in `/blog/index.xml`. The three newest posts also show
on the landing page. Add each new post to `static/llms.txt`.

Hold the blog to the same bar as the docs: every number and claim should trace
to the code, a run you actually did, or a cited source. For comparison posts,
check claims against the other project's current README, date the comparison,
and say where the other tool is ahead.

