# Court Sheet: NCAA D-I men's basketball predictions

Live site: https://gh0stmahn-ai.github.io/cbb-predictor/

A tempo-free rating model (adjusted offense, defense, pace) with a roster-based preseason prior,
fitted home court, betting line blending, and a 50,000-run tournament simulator.

## How it runs
GitHub Actions (`.github/workflows/update.yml`) runs `python -m pipeline.run daily` three times a day:
ingests final scores and box scores from ESPN, captures betting lines before tip, refits ratings,
locks predictions for games not yet tipped, grades finished games, and rebuilds the bracket.
The first Monday run each week freezes a snapshot in `docs/data/weeks/` that is never edited.
Use **Actions > Update predictions > Run workflow** to refresh by hand.

## Layout
- `model/` ratings, preseason prior, bracket simulator, ESPN ingestion, backtest code
- `pipeline/run.py` the daily job
- `state/` model memory: games, betting lines, locked prediction ledger, last seasons' ratings
- `docs/` the website (GitHub Pages serves this folder)
- `config.json` season dates, Selection Sunday, Final Four region pairings, blend settings

## Each March
On Selection Sunday, set `final_four_pairs` for the season in `config.json`
(which regions meet in the national semifinals). Selection Sunday dates are in the same file.

## Backtest (walk-forward, weekly refits)
Tuned on 2014 to 2020; untouched holdout 2022 to 2026: 71.9% winners, log loss 0.535, margin MAE 9.1.
NCAA tournament games 2014 to 2026: 70.3% winners, log loss 0.541.
To rebuild: `python -c "from model.data import load_all; load_all(range(2012,2027)).to_parquet('raw/games.parquet')"`
then `model/backtest.py`.
