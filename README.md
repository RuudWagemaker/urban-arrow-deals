# Deal Tracker

Tracks Marktplaats ads for the products you follow and estimates a fair price for every
listing. It currently follows **Urban Arrow** cargo bikes and the **Honeywell T6** smart
thermostat, with one website and a product switcher. It runs on GitHub for free:

```
every 2 hours, 07:00–23:00 (GitHub Actions), for every product in config.yaml
  → Marktplaats search (+ optional shop pages for new items, + optional 2dehands.be)
  → Claude Haiku 5.5 reads each new ad → the product's details (bike: variant, year, motor,
    battery, km...; thermostat: T6 or T6R, condition, receiver, box...) and red flags
  → price history, sold/removed detection, relist detection   (data/<product>/listings.json)
  → fair price + deal score for every ad
  → website on GitHub Pages  +  phone/email alerts for new deals and price drops
```

## Products and profiles

Each product in `config.yaml` (under `products`) has a **profile** that says what Claude
extracts from an ad and how ads are compared:

| Profile | Used for | Compared on |
|---|---|---|
| `cargo_bike` | Urban Arrow | variant, age, motor, battery, mileage, extras, dealer |
| `thermostat` | Honeywell T6 | T6 (wired) or T6R (wireless), condition (sealed → used), receiver, stand, box; per unit for multi-packs |

To follow another product of a kind that already has a profile, copy a product block in
`config.yaml` and change its name, searches and list prices. A new kind of product needs a
new profile in `uatracker/profiles/`.

## What you get (per product)

- **Today tab:** the ads that appeared today, yesterday or in the last 7 days.
- **Second-hand tab** ("All ads" for thermostats): every listing with its asking price, a fair-price range and a label
  (great deal / good deal / fair price / a bit pricey / overpriced). It also shows distance
  from Den Bosch, price drops and warning signs. Under "Details" you can see which similar
  bikes the estimate is based on.
- **New tab:** new bikes from shops you add, plus new bikes listed on Marktplaats. Each is
  compared with the list price and the cheapest offer for that variant.
- **Market tab:** median prices, how much each feature adds or removes and, for bikes,
  price by model year and a depreciation curve.

## How the fair price is calculated

See `uatracker/pricing.py`. In short:

1. **Adjusted comparables:** the up to 12 most similar bikes (same variant, close in age,
   battery, motor, mileage and seller type). Each one's price is corrected for its
   differences, and the fair price is their weighted median.
2. Asking prices aren't sale prices. Listings that disappear within 30 days (probably sold)
   count 1.5×, and listings still unsold after 60 days count 0.6×.
3. With fewer than 4 comparables it falls back to a regression model. With fewer than about
   25 priced listings in total it falls back to the list price × a depreciation curve
   (shown as *low confidence*).

**It gets better as data builds up.** The first week is mostly low/medium confidence. After
a few weeks you'll have a few hundred listings and estimates you can trust.

## Setup (about 15 minutes)

### 1. Put the code on GitHub
Create a repository, push this folder, then go to **Settings → Pages → Source → GitHub Actions**.
(GitHub Pages on a free account needs a public repository. The site only shows public
listing data.)

### 2. Let Claude read the listings (pick one)
**A: Your Claude Pro subscription (no extra cost).** On your own computer:
```bash
npm install -g @anthropic-ai/claude-code
```
```bash
claude setup-token
```
Copy the token and add it as repository secret **`CLAUDE_CODE_OAUTH_TOKEN`**
(Settings → Secrets and variables → Actions). The daily run uses Claude Haiku 5.5 on
about 10–40 listings, which uses very little of your Pro allowance.

**B: An API key** from console.anthropic.com, saved as secret **`ANTHROPIC_API_KEY`**.
This costs well under €1 a month at this volume.

Without either, the tracker still works using only built-in text rules, but it's less accurate.

### 3. Alerts (optional)
Choose either or both.

**Phone notifications with ntfy (no account, no password)**
1. Install the free **ntfy** app (iOS App Store / Google Play).
2. Make up a long random channel name, e.g. `ua-deals-` followed by 16 random letters and
   numbers. Anyone who knows the name can read the alerts, so keep it unguessable.
3. In the app, tap **+**, enter the name, and subscribe (server: ntfy.sh).
4. Add the name as repository secret **`NTFY_TOPIC`**.

Each new deal arrives as its own notification with a photo. Tap it to open the listing.

**Email through Gmail**
1. Create an app password (needs 2-Step Verification) at
   https://myaccount.google.com/apppasswords.
2. Add secrets **`GMAIL_ADDRESS`** and **`GMAIL_APP_PASSWORD`**. Optionally add **`EMAIL_TO`**.

The first alert lists the best current deals. After that you're only alerted when a new
listing is at least 10% below its fair price within 120 km, or when a deal you were told
about drops in price. Change these thresholds in `config.yaml`.

### 4. First run
Go to **Actions → Daily Urban Arrow scan → Run workflow**. After that it runs every 2 hours
between about 07:00 and 23:00 Dutch time.

## Settings (`config.yaml`)

- `home` – Den Bosch is set. Distance is calculated from here.
- `alerts` – default alert thresholds; each product can override them (the Honeywell T6
  alerts at 15% below fair price, anywhere in the country, since thermostats are shipped).
- Per product, under `products:`
  - `marktplaats.queries` – the searches to run.
  - `new_item_pages` – product pages of shops selling the item new (any web shop with
    standard product data works).
  - `msrp_estimate` – **rough guesses** of new prices, only used before enough data exists.
    Please check and correct them.
  - `tweedehands_be.enabled` – also search 2dehands.be (Belgium).
- `llm.model` – `claude-haiku-5-5` by default. You can switch to `claude-sonnet-5-5` if
  extraction quality ever disappoints, but Haiku handles this task well.

## Run it locally

```bash
pip install -r requirements.txt
```
```bash
python -m uatracker run
```
```bash
python -m http.server -d site 8000
```
Run the tests with `python -m pytest`.

## Caveats

- **Marktplaats' terms don't allow automated collection.** This tool runs once a day, at a
  gentle pace, for personal use. Marktplaats may also block GitHub's servers. If the
  Actions log says `HTTP 403 (blocked?)`, run the same command on a computer at home with
  a daily cron job or Task Scheduler, followed by `git push`. Another option is to set up a
  Marktplaats saved search with email alerts.
- The Marktplaats search endpoint isn't a public API and can change. If it breaks, the job
  logs an error and keeps the previous data. The fix is usually a few lines in
  `uatracker/sources/marktplaats.py`.
- "Fair price" is based on asking prices from listings, not confirmed sale prices.
  Always look at the bike and check the frame number and receipt before buying.
