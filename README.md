# Charger occupancy monitor (EDP vs Iberdrola / Repsol / Endesa)

Scrapes charging-point status from MapaREVE every 5 minutes for a set of nearby
locations, logs each reading, and shows a live dashboard comparing occupancy by
operator. Built to run **free for ~a week** with no server and nothing left
running on your own computer.

## How it works

- **`scraper.py`** opens each location page with a headless browser (Playwright),
  reads the operator name, each charging point's status (Available / Occupied /
  Out of service / …), the "since" time, power and connector, and appends one row
  per charging point to **`data/readings.csv`**.
- **`.github/workflows/scrape.yml`** runs that scraper every 5 minutes on GitHub's
  servers and commits the updated CSV.
- **`index.html`** is the dashboard. It reads `data/readings.csv` and charts
  occupancy over time per operator, plus the current status of every point.

**Occupancy rate** here = `(occupied + reserved) ÷ operational connectors`, where
operational excludes out-of-service points. Change this in `index.html`
(`IN_USE` / `OPERATIONAL` sets) and in any analysis if you prefer another rule.

## Free setup (GitHub Actions + GitHub Pages)

You need a free GitHub account. Use a **public** repository — public repos get
unlimited free Actions minutes, which a 5-minute schedule needs.

1. **Create a new public repo** and upload every file here, keeping the folder
   structure (`scraper.py`, `locations.json`, `requirements.txt`, `index.html`,
   `.nojekyll`, `data/readings.csv`, and `.github/workflows/scrape.yml`).
2. **Turn on Actions:** repo → *Settings → Actions → General* → allow actions.
   Then open the *Actions* tab → **scrape-chargers** → *Run workflow* to fire one
   run now and confirm it works (check that `data/readings.csv` gets new rows).
3. **Turn on the dashboard:** *Settings → Pages* → Source = *Deploy from a branch*
   → Branch `main` / folder `/ (root)` → Save. After ~1 minute your dashboard URL
   appears (e.g. `https://<you>.github.io/<repo>/`).
4. **Open the dashboard.** Until enough readings pile up it shows a sample preview
   (with a banner saying so); it switches to your real data as the workflow commits.

The schedule then runs on its own. **GitHub's scheduled jobs aren't perfectly
punctual** — they can run a few minutes late or skip a slot under load, so you'll
capture most 5-minute readings but not a flawless metronome. That's fine for
occupancy trends.

## Editing the locations

`locations.json` lists the 8 URLs grouped into 3 proximity clusters. For each you can set:
- `cluster` — the proximity group ("Location 1/2/3"); the dashboard renders one
  EDP-vs-neighbours comparison per cluster, plus an overall summary on top.
- `label` — a friendly name shown on the dashboard (e.g. "EDP", "Repsol").
- `operator` — optional override; if blank, the scraper uses the name printed on
  the page. The dashboard buckets operators by keyword (edp / iberdrola / repsol /
  endesa), so brand variants like "Endesa X" still group, and the two Endesa sites
  in Location 3 combine into one Endesa figure.

## Stopping after your week

*Actions* tab → **scrape-chargers** → ⋯ → **Disable workflow**. Nothing keeps
running and there is nothing to pay or cancel. Delete the repo if you want it gone.
Your collected `data/readings.csv` is yours to keep / download for further analysis.

## Running locally instead (alternative, also free)

Your machine must stay on and awake for the whole week.

```bash
pip install -r requirements.txt
python -m playwright install chromium
python scraper.py                 # one reading now
python -m http.server 8000        # then open http://localhost:8000 for the dashboard
```

Schedule it every 5 min with cron (Mac/Linux): `*/5 * * * * cd /path && python scraper.py`
or Task Scheduler on Windows.

## Troubleshooting the scrape

First run a check that also saves what the page looked like:

```bash
python scraper.py --debug          # writes debug/<id>.html and debug/<id>.txt
```

- **Statuses come back as `unknown`** → the site used wording the classifier didn't
  know. Open a `debug/*.txt`, find the exact status word, and add it to
  `STATUS_RULES` in `scraper.py`.
- **`no_points_parsed` in the CSV** → the page layout differs from expected. Send
  me a `debug/*.html` and I'll adjust the selectors.

## Notes / limits

- Cost: **$0** on the free tiers described.
- A public repo means the code and the collected availability data are public.
  MapaREVE charger availability is already public information; just don't add any
  secrets to the repo.
- Be a good citizen: 7 pages every 5 minutes is light traffic. Don't crank the
  interval far lower.
