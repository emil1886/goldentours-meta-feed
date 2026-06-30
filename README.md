# goldentours-meta-feed

Builds a Meta (Facebook/Instagram) Commerce catalogue feed for Golden Tours and
publishes it via GitHub Pages so Meta can fetch it on a schedule.

- **Feed (CSV):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.csv`
- **Feed (XML):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.xml`

The crawler discovers products from the site's HTML sitemap, reads each product
page's JSON-LD offer (the authoritative price Meta matches against), and writes a
Meta-compliant feed into `docs/`, which Pages serves.

## The one hard requirement: a UK IP

The site prices by **server-side IP geolocation** — a UK IP gets GBP, a US IP
gets USD, and no cookie/parameter overrides it. The feed must therefore be built
from a UK connection. GitHub's hosted runners are US-based, so the daily workflow
routes requests through a **UK proxy**.

### Set this up once

Add a repository secret so the scheduled build can reach the site as a UK
visitor:

1. Repo **Settings → Secrets and variables → Actions → New repository secret**
2. Name: `UK_PROXY`  Value: `http://user:pass@<uk-proxy-host>:<port>`

Until that secret exists, the daily run fails fast (by design) rather than
publishing a USD feed. The previously published feed stays live untouched.

## Guards (a bad run never publishes)

The build aborts **before writing** if:

- any product is not in GBP (`--strict-currency`) — i.e. the crawl wasn't on a UK IP, or
- fewer than `--min-products` products were found (`--min-products 300`) — i.e. the
  site or sitemap changed.

On abort the workflow fails red and commits nothing, so Meta keeps reading the
last good feed.

## Run it locally (from the UK)

```bash
pip install requests beautifulsoup4 lxml
python goldentours_meta_feed_gbp.py --currency GBP --strict-currency \
  --min-products 300 --out-dir docs
```

## Meta setup

Commerce Manager → Catalogue → Data Sources → Add Items → **Data Feed** →
**Use a URL** → paste the CSV URL above → schedule **Daily**.
