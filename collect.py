#!/usr/bin/env python3
"""Pull settled Kalshi tennis markets + in-play price history. Public endpoints only, no API key.

Output (data/):
  markets.csv.gz   one row per settled market (match-winner and set-winner series)
  candles/<series>_<YYYY-MM>.csv.gz   1-minute bid/ask/last for each match-winner market,
                                      covering the final HOURS_LIVE hours before close,
                                      plus hourly candles from market open (pre-match price)
  done.txt         match markets already fetched (safe to re-run / resume)
"""
import csv, gzip, json, os, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

BASE = os.environ.get("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
# Match-winner series we price, and set-winner series that tell us who won set 1 and when.
# Unknown/empty series are skipped automatically.
MATCH_SERIES = ["KXATPMATCH", "KXWTAMATCH", "KXATPCHALLENGERMATCH", "KXWTACHALLENGERMATCH", "KXITFWMATCH", "KXITFMATCH"]
SET_SERIES = ["KXATPSETWINNER", "KXWTASETWINNER", "KXATPCHALLENGERSETWINNER", "KXWTACHALLENGERSETWINNER",
              "KXITFWSETWINNER", "KXITFSETWINNER"]
HOURS_LIVE = 8
SLEEP = float(os.environ.get("KALSHI_SLEEP", "0.12"))
MAX_MARKETS = int(os.environ.get("MAX_MARKETS", "0"))  # 0 = no cap (use a small number to smoke-test)
OUT = os.environ.get("OUT_DIR", "data")


def get(path, params=None, tries=6):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "tennis-research/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(SLEEP)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(min(60, 2 ** i))
        except Exception:
            time.sleep(min(60, 2 ** i))
    return None


def ts(s):
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def px(d, k):
    """price from a candle sub-object; handles '<k>_dollars' strings and integer-cent '<k>'."""
    if not isinstance(d, dict):
        return ""
    v = d.get(k + "_dollars")
    if v not in (None, ""):
        return round(float(v), 4)
    v = d.get(k)
    if v in (None, ""):
        return ""
    v = float(v)
    return round(v / 100 if v > 1 else v, 4)


def list_markets(series):
    """settled markets for a series from both the live and the archived (historical) stores."""
    seen = {}
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cursor = ""
        while True:
            p = {"series_ticker": series, "limit": 1000, **extra}
            if cursor:
                p["cursor"] = cursor
            j = get(path, p)
            if not j:
                break
            for m in j.get("markets", []):
                m["_store"] = "hist" if path.startswith("/hist") else "live"
                seen.setdefault(m["ticker"], m)
            cursor = j.get("cursor") or ""
            if not cursor or not j.get("markets"):
                break
    return list(seen.values())


def candles(series, m, start, end, interval):
    rows = []
    step = 4500 * interval * 60
    a = start
    while a < end:
        b = min(end, a + step)
        p = {"start_ts": a, "end_ts": b, "period_interval": interval}
        j = None
        order = ("hist", "live") if m["_store"] == "hist" else ("live", "hist")
        for store in order:
            path = (f"/historical/markets/{m['ticker']}/candlesticks" if store == "hist"
                    else f"/series/{series}/markets/{m['ticker']}/candlesticks")
            j = get(path, p)
            if j and j.get("candlesticks"):
                break
        for c in (j or {}).get("candlesticks", []):
            rows.append([m["ticker"], interval, c.get("end_period_ts"),
                         px(c.get("yes_bid"), "close"), px(c.get("yes_ask"), "close"),
                         px(c.get("yes_bid"), "low"), px(c.get("yes_ask"), "high"),
                         px(c.get("price"), "close"), px(c.get("price"), "low"), px(c.get("price"), "high"),
                         c.get("volume_fp", c.get("volume", ""))])
        a = b
    return rows


def main():
    os.makedirs(f"{OUT}/candles", exist_ok=True)
    cols = ["series", "kind", "ticker", "event_ticker", "title", "yes_sub_title", "open_time", "close_time",
            "settlement_ts", "result", "settlement_value", "volume", "store"]
    allm = []
    with gzip.open(f"{OUT}/markets.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for kind, lst in (("match", MATCH_SERIES), ("set", SET_SERIES)):
            for s in lst:
                ms = list_markets(s)
                print(f"{s}: {len(ms)} settled markets", flush=True)
                for m in ms:
                    w.writerow([s, kind, m["ticker"], m.get("event_ticker"), m.get("title"), m.get("yes_sub_title"),
                                m.get("open_time"), m.get("close_time"), m.get("settlement_ts"), m.get("result"),
                                m.get("settlement_value_dollars", m.get("settlement_value", "")),
                                m.get("volume_fp", m.get("volume", "")), m["_store"]])
                    if kind == "match":
                        m["_series"] = s
                        allm.append(m)
    done_path = f"{OUT}/done.txt"
    done = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    todo = [m for m in allm if m["ticker"] not in done and m.get("close_time")]
    todo.sort(key=lambda m: m["close_time"], reverse=True)
    if MAX_MARKETS:
        todo = todo[:MAX_MARKETS]
    print(f"fetching candles for {len(todo)} match markets ({len(done)} already done)", flush=True)
    t0 = time.time()
    budget = float(os.environ.get("MAX_SECONDS", "0"))
    with open(done_path, "a") as dfile:
        for i, m in enumerate(todo):
            if budget and time.time() - t0 > budget:
                print("time budget reached; re-run to resume", flush=True)
                break
            close, opn = ts(m["close_time"]), ts(m.get("open_time")) or ts(m["close_time"]) - 86400
            live_start = max(opn, close - HOURS_LIVE * 3600)
            rows = candles(m["_series"], m, live_start, close + 60, 1)
            if opn < live_start:
                rows += candles(m["_series"], m, max(opn, live_start - 14 * 86400), live_start, 60)
            month = m["close_time"][:7]
            with gzip.open(f"{OUT}/candles/{m['_series']}_{month}.csv.gz", "at", newline="") as f:
                csv.writer(f).writerows(rows)
            dfile.write(m["ticker"] + "\n")
            dfile.flush()
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}  {m['ticker']}  rows={len(rows)}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
