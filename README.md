# Urban Arrow Deals

A daily tracker of new and second-hand Urban Arrow cargo bikes in the Netherlands, with a
fair-price estimate for every second-hand listing. It runs on GitHub for free:

```
every morning (GitHub Actions)
  → Marktplaats search (+ optional shop pages for new bikes, + optional 2dehands.be)
  → Claude Haiku 5.5 reads each new listing → variant, year, motor, battery, gear hub, km, extras, red flags
  → price history, sold/removed detection, relist detection   (data/listings.json, committed daily)
  → fair price + deal score for every second-hand bike
  → website on GitHub Pages  +  Telegram message with new deals and price drops
```

## What you get

- **Second-hand tab:** every listing with its asking price, a fair-price range and a label
  (great deal / good deal / fair price / a bit pricey / overpriced). It also shows distance
  from Den Bosch, price drops and warning signs. Under "Details" you can see which similar
  bikes the estimate is based on.
- **New tab:** new bikes from shops you add, plus new bikes listed on Marktplaats. Each is
  compared with the list price and the cheapest offer for that variant.
- **Market tab:** price by model year, a depreciation curve, and how much each feature
  (age, motor, battery, Enviolo, rain tent, dealer) adds or removes.

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

### 3. Telegram alerts (optional)
1. In Telegram, message **@BotFather**, send `/newbot` and copy the token.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Add secrets **`TELEGRAM_BOT_TOKEN`** and **`TELEGRAM_CHAT_ID`**.

You'll get a message when a new listing is at least 10% below its fair price within
120 km, or when a good deal drops in price. Change these thresholds in `config.yaml`.

### 4. First run
Go to **Actions → Daily Urban Arrow scan → Run workflow**. After that it runs every
morning around 06:30.

## Settings (`config.yaml`)

- `home` – Den Bosch is set. Distance is calculated from here.
- `new_bike_pages` – add product pages of shops selling new Urban Arrows (any web shop
  with standard product data works). Find dealers via the dealer locator on urbanarrow.com.
- `msrp_estimate` – **rough guesses**, only used before enough data exists. Please check
  and correct them against current list prices.
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
