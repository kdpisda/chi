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
- Styles: `assets/css/main.css` (dark, terminal-native; system font stack +
  monospace accents).

Keep docs grounded in the code and the specs under the repo's `docs/`
directory — no invented benchmarks or features.
