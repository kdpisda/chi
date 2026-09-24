---
title: "Privacy Policy"
description: "What getchi.dev and the chi newsletter collect, why, and your choices. The chi software itself collects nothing."
date: 2026-09-24
---

*Last updated: 24 September 2026*

This Privacy Policy explains what information this website (`getchi.dev`) and the
chi newsletter collect, why, and what you can do about it. It also explains what
the chi software does *not* collect.

## Who is responsible for your data

This website and the newsletter are operated by
[HappyChases Media Works OPC Private Limited](https://happychases.com)
(“HappyChases”), a One Person Company incorporated in India, on behalf of
Kuldeep Pisda, the author and owner of chi. HappyChases decides how the personal
data described below is used. Under India’s Digital Personal Data Protection Act,
2023 it is the *data fiduciary*.

For any privacy request or grievance, contact Saina Bisht at
**[saina.bisht@happychases.com](mailto:saina.bisht@happychases.com)**.

## The chi software collects nothing

The chi command-line tool (`getchi` on PyPI, run as `chi`) runs on your own
machine and **sends no telemetry, analytics or usage data to us**. Specifically:

- API keys you give chi are stored locally, in `.env` or
  `~/.config/chi/credentials.env` (created with `0600` permissions). They never
  leave your machine except in requests to the provider they belong to.
- Your prompts, code and run data go **directly** from your machine to the LLM
  providers and command-line tools you configure (for example Anthropic, OpenAI or
  DeepSeek), and to any web pages the agents fetch. Those services handle that data
  under their own terms and privacy policies. We never receive it.
- Run records (the SQLite + JSONL store, champions, logs) stay in your local
  `runs/` directory.

Downloading chi from PyPI or GitHub, or opening issues and pull requests on
GitHub, is governed by those platforms’ own policies.

## What the website and newsletter collect

- **Newsletter details.** If you subscribe, we store the email address (and name,
  if given) you provide, along with the consent you gave. The newsletter runs on a
  self-hosted Sendy instance operated by HappyChases at `marketing.happychases.com`,
  and emails are delivered through Amazon SES. We record opens and link clicks in
  our emails, so we can tell which posts are useful.
- **Usage analytics.** We use Google Analytics 4 to understand aggregate traffic:
  pages visited, referrer, approximate (city-level) location, and device and
  browser type. This is collected via cookies and is not used to identify you
  personally.
- **Hosting logs.** The site is hosted on GitHub Pages, with DNS by Cloudflare.
  Like any web host, they process standard technical data (IP address, timestamp,
  requested URL) for security and reliability.

## How we use it

- To send you the newsletter you asked for (one email per new blog post), and
  nothing you didn’t ask for.
- To understand what content is useful and improve the site.
- To keep the site secure and operational.

We do **not** sell your data, and we share it only with the service providers
named below, strictly to run the services above.

## Who we share it with

- **Sendy (self-hosted by HappyChases) and Amazon SES**: newsletter delivery
  (email, name, open and click events).
- **Google Analytics**: aggregate, cookie-based usage analytics.
- **GitHub Pages and Cloudflare**: hosting and DNS.

Some of these providers process data outside India.

## Your choices and rights

- **Unsubscribe** from the newsletter at any time using the link in any email, or
  by emailing Saina at the address above.
- **Access, correct or delete** the personal data we hold about you. Email Saina and
  we will act on it within a reasonable time. You may also withdraw your consent at
  any time, and raise a grievance with us about how your data is handled.
- **Opt out of analytics** by blocking cookies in your browser or using Google’s
  [opt-out tools](https://tools.google.com/dlpage/gaoptout). See the
  [Cookie Policy](/legal/cookies/) for details.

## Retention

We keep newsletter data until you unsubscribe or ask us to delete it. Google
Analytics keeps analytics data for its default retention period.

## Children

This site and newsletter are meant for software developers and aren’t directed at
children. We don’t knowingly collect data from anyone under 18.

## Changes

We may update this policy. The “last updated” date at the top shows when it last
changed.

## Contact

Questions about this policy? Email Saina Bisht at
**[saina.bisht@happychases.com](mailto:saina.bisht@happychases.com)**.
