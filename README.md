# goldentours-meta-feed

Builds a Meta (Facebook/Instagram) Commerce catalogue feed for Golden Tours and
publishes it via GitHub Pages so Meta can fetch it on a schedule.

- **Feed (CSV):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.csv`
- **Feed (XML):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.xml`

The crawler discovers products from the site's HTML sitemap, reads each product
page's JSON-LD offer (the authoritative price Meta matches against), and writes a
Meta-compliant feed into `docs/`, which Pages deploys.

## How it runs

A GitHub Action (`.github/workflows/goldentours-feed.yml`) runs **daily**,
rebuilds the feed, commits it to `main` for history, and deploys `docs/` to
GitHub Pages. **No proxy, no server, no manual step** — it's fully automated.

## Currency (the interesting bit)

The site shows prices in the visitor's IP-geolocated currency (UK IP -> GBP, US
IP -> USD), and a self-supplied `curr` cookie or `?curr=` parameter is *ignored*.
But the on-page currency selector is a **POST form** (`form#currform` -> `POST
curr=<code>`), and the server honours that by issuing a "blessed" currency cookie
that then sticks for the whole session **regardless of IP**. `establish_currency()`
mirrors this, so the crawl renders GBP even from GitHub's US-based runners.

## Guards (a bad run never publishes)

The build aborts **before writing/deploying** if:

- any product is not in GBP (`--strict-currency`) — i.e. the POST override didn't take, or
- fewer than `--min-products` products were found (`--min-products 300`) — i.e. the
  site or sitemap changed.

On abort the workflow fails red and deploys nothing, so Meta keeps reading the
last good feed.

## Run it locally

```bash
pip install requests beautifulsoup4 lxml
python goldentours_meta_feed_gbp.py --currency GBP --strict-currency \
  --min-products 300 --out-dir docs
```

Works from any connection — `establish_currency()` forces GBP via the POST
selector.

## Meta setup

Commerce Manager → Catalogue → Data Sources → Add Items → **Data Feed** →
**Use a URL** → paste the CSV URL above → schedule **Daily**.
