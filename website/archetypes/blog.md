---
title: "{{ replace .File.ContentBaseName "-" " " | title }}"
# 140–160 chars. Used as the meta description, the social-card text, the
# listing teaser, and the RSS summary — write it for a search result.
description: ""
date: {{ .Date }}
draft: true
# One of: Guide · Deep dive · Comparison · Release · Case study
category: "Guide"
# Free-form keywords (article:tag + JSON-LD keywords). No tag pages are built.
tags: []
# Defaults to site.Params.author. Set for a guest post.
# author: ""
# Sidebar "on this page" nav — worth it for posts with 4+ sections.
toc: false
# Optional 1200×630 social card. A file next to index.md in a page bundle
# (e.g. "cover.png") or a site path (e.g. "/img/foo.png"); defaults to /og.png.
# image: ""
# The email sent to newsletter subscribers when this post goes live (see
# "Newsletter" in website/README.md). Write it for the inbox, not a copy of the
# post: title, date, greeting, "Read the post" button and unsubscribe footer are
# added by the template. Only [links](https://...) and `inline code` render.
newsletter:
  subject: ""     # ~60 chars; not just the post title
  preheader: ""   # one line of grey preview text after the subject
  # body: 2-4 short plain-text paragraphs (what's new, why it matters to the
  # reader, a tease of the key insight), separated by blank lines. Left empty,
  # the email falls back to the title + description.
  body: ""
---

Lead with the problem the reader has, in one or two sentences.

## First section

Keep claims grounded in the code and the specs under the repo's `docs/` —
link to the docs page or the source file that backs a number.
