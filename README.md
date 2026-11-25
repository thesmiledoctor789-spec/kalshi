# Kalshi tennis research

Tests one idea: buy a heavy pre-match favorite (-200 or shorter) on Kalshi after they lose the first set.

- `collect.py` pulls every settled Kalshi tennis match-winner and set-winner market, plus minute-by-minute
  bid/ask for the match markets. It uses public market-data endpoints only. **No API key is needed, and none
  should ever be committed to this repo.**
- `.github/workflows/collect.yml` runs the collector on GitHub's servers and commits the results to `data/`.

Run it: Actions tab -> "Collect Kalshi tennis data" -> Run workflow. Or locally: `python collect.py`
(`MAX_MARKETS=50 python collect.py` for a quick test).
