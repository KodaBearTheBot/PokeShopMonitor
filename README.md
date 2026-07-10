# Shop New-Product Watcher

Checks shop pages every 15 minutes and pushes a notification to your phone
the moment a new product shows up.

## One-time setup (about 10 minutes)

### 1. Install ntfy on your phone
- iOS: search "ntfy" in the App Store
- Android: search "ntfy" in the Play Store, or get it from F-Droid

Open the app, tap "+", and **subscribe to a topic name**. This is like a
private channel name — pick something long and random that nobody would
guess, e.g. `luke-pokemon-drops-x7q2`. Write it down, you'll need it below.
(Topics on ntfy.sh are public-by-obscurity: anyone who knows the exact name
could technically also subscribe, so don't use something guessable like
"pokemon".)

### 2. Create a free GitHub account (if you don't have one)
https://github.com/signup

### 3. Create a new repository
- Click "New repository"
- Name it anything, e.g. `shop-monitor`
- Keep it **Private** (recommended) or Public, your choice
- Click "Create repository"

### 4. Upload these files
Upload all the files from this project (keeping the folder structure,
especially the `.github/workflows/check-shops.yml` path) to your new repo.
Easiest way: on the repo page, click "Add file" → "Upload files", drag
everything in, and commit.

### 5. Add your ntfy topic as a secret
- In your repo: Settings → Secrets and variables → Actions → New repository secret
- Name: `NTFY_TOPIC`
- Value: the topic name you picked in step 1
- Save

### 6. Turn it on
- Go to the "Actions" tab in your repo
- You should see the "Check shops for new products" workflow
- Click it, then "Run workflow" to do a manual first test run
- Check the run's logs — first run just saves a baseline (no notification,
  that's expected — it doesn't know what's "old" vs "new" yet)
- Run it a second time manually (or just wait for the schedule) after
  changing something to confirm you get a push

From here it runs automatically every 15 minutes, for free, forever
(GitHub gives every account 2,000+ free Action minutes/month, this uses
almost none of it).

## Shops currently configured

1. **sunvi.de** – Pokemon category page
2. **luminous.cards** – Pokemon Pre-Orders page
3. **games-island.eu** – Pokemon category (uses their official bot-friendly
   data feed at crawlme.games-island.eu, per their own published rules —
   this one's actually the most reliable of the five)
4. **pushdich-tcg.de** – Pokemon (German) collection (Shopify)
5. **tcgviert.com** – Vorbestellungen (pre-orders) — ⚠️ this specific page
   returned "no products found" when checked, possibly because it's gated
   behind a customer-account lock (their site has an "EasyLockdown" app
   installed). Watch the first Action run's logs for this one specifically;
   if it comes back empty every time, tell me and I'll dig into an
   alternative page/URL for that shop.

## Adding more shops

Open `shops.json` and add another entry:

```json
{
  "id": "some_short_unique_id",
  "name": "Display Name For Notifications",
  "url": "https://example-shop.com/pokemon-category-page",
  "platform": "generic"
}
```

`platform` can be:
- `"generic"` – works for most shops (tries structured SEO data first, falls
  back to a generic link-scan)
- `"shopify"` – for shops built on Shopify (URLs containing `/collections/`)
  — uses Shopify's own product-list feed instead of scraping, much more
  reliable
- `"games_island"` – specific to games-island.eu's bot data feed

Use the specific category/collection page where new items would appear —
not the homepage.

## Notes
- Detection works by comparing today's product list to yesterday's.
- If a shop's "new" detection ever misses things or comes back empty, send
  me the shop name and I can look into tuning it, or check the Action's log
  output for that run — it prints a warning when a shop returns 0 products.
- If you ever want a different check frequency, edit the `cron:` line in
  `.github/workflows/check-shops.yml` (currently `*/15 * * * *` = every 15 min).
- Being nice to the shops: 15 min is a reasonable interval for 5 shops. If
  you add a lot more shops, consider spacing checks out more (e.g. every
  20-30 min) so you're not hammering smaller sites.
