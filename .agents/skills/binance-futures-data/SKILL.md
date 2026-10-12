---
name: binance-futures-data
description: Use when you need historical Binance USD-M futures data for backtesting or analysis - candles (1m, 1h, 1d), funding rates, open interest and long/short ratios, and daily taker order-flow dollars by trade size. Explains the S3 bucket layout, schemas, access and pitfalls.
---

# Binance USD-M futures data (S3)

A private S3 bucket holds the history of Binance USD-M futures as Parquet files: candles, funding rates, and open interest with long/short ratios. Use it for any historical data on these contracts. It is far faster than the Binance API for history, and it includes delisted contracts, which the API does not serve.

## What is and is not here

Here:
- **Candles (klines)** for every USD-M futures contract, perpetual and dated delivery, live and delisted. Resolutions: 1 minute, 1 hour, 1 day. About 1.05 billion 1m rows, from January 2020.
- **Funding rates** for every perpetual, one row per settlement, from January 2020.
- **Metrics** at 5-minute resolution, from September 2020: open interest, plus long/short and taker buy/sell ratios. Also a daily snapshot file.
- **Taker flow** daily per symbol, from January 2020: taker buy and sell dollars and trade counts split into six trade-size buckets, built from Binance's aggTrades dumps, with net and gross summary columns for the whole day and for the small (under $1k) and large ($100k and over) tails.

Not here:
- Spot or COIN-M data. Symbol names such as BTCUSDT match spot names, but everything in this bucket is the futures contract.
- Liquidations, mark price, index price, order books. Individual trades are not kept either — only their daily taker-flow aggregation above.
- The most recent hours. For live or very recent values, call the Binance futures API directly.

Prices are last traded price, not mark price. Liquidations and funding payments on Binance are computed from mark price, so treat liquidation modelling from this data as approximate.

## Access

- Bucket: `binance-data-546016014952-eu-west-2-an`
- Region: `eu-west-2`
- Root prefix: `v1/binance/um/`
- Credentials: secret `AWS_S3_ACCESS_KEY` is the AWS access key ID, and secret `AWS_S3_ACCESS_KEY_SECRET` is the AWS secret access key. Access is read-only.

Most tools look for the standard AWS names, so map the secrets explicitly:

```python
import os
key = <value of secret AWS_S3_ACCESS_KEY>
secret = <value of secret AWS_S3_ACCESS_KEY_SECRET>
os.environ["AWS_ACCESS_KEY_ID"] = key
os.environ["AWS_SECRET_ACCESS_KEY"] = secret
os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
```

Never print, log or write the secret values to a file.

## Layout

```
s3://binance-data-546016014952-eu-west-2-an/v1/binance/um/
  klines/
    interval=1m/
      symbol=<SYMBOL>/
        year=<YYYY>/data.parquet     one file per symbol per calendar year
    interval=1h/
      year=<YYYY>/data.parquet       all symbols together, one file per year
    interval=1d/
      data.parquet                   all symbols, all history, one file
  funding/
    data.parquet                     all perpetuals, all history, one file
  metrics/
    interval=5m/
      symbol=<SYMBOL>/
        year=<YYYY>/data.parquet     one file per symbol per calendar year
    interval=1d/
      data.parquet                   all symbols, all history: last 5m row of each UTC day
  taker_flow/
    interval=1d/
      data.parquet                   all symbols, all history: taker $ by trade size, one row per symbol-day
  meta/
    symbols.parquet                  candle coverage: one row per symbol
    metrics_symbols.parquet          metrics coverage: one row per symbol
    manifest.json                    freshness of every dataset
```

Ignore anything under `v1/_test/` and the file `meta/_write_test`. They are leftovers from setup.

The `key=value` folder names follow the Hive convention. With hive partitioning enabled, DuckDB, Polars and PyArrow expose `symbol` and `year` as columns and skip files that a filter rules out.

### Which file to use

| Need | Use | Rough size |
|---|---|---|
| Universe selection, ranking, daily strategies | `klines/interval=1d/data.parquet` | about 35 MB, under 1M rows |
| Screening across many symbols, hourly strategies | `klines/interval=1h/year=YYYY/` | 50 to 200 MB per year |
| Minute-level backtests on specific symbols | `klines/interval=1m/symbol=X/year=YYYY/` | 10 to 20 MB per symbol-year, about 526k rows |
| Funding carry, funding as a signal, funding costs in a backtest | `funding/data.parquet` | a few million rows, tens of MB |
| Daily open interest or positioning across the universe | `metrics/interval=1d/data.parquet` | under 1M rows |
| Intraday open interest or ratios for specific symbols | `metrics/interval=5m/symbol=X/year=YYYY/` | a few MB per symbol-year, about 105k rows |
| Order-flow imbalance, flow by trade size, retail vs large-print activity | `taker_flow/interval=1d/data.parquet` | about 90 MB, under 1M rows |

Start with the coarsest file that answers the question. Screen on daily or hourly data, then pull 1m candles or 5m metrics only for the symbols and periods that matter.

## Schemas

All `time` columns are timestamps in UTC with millisecond precision.

### Candles: 1m files

| Column | Type | Meaning |
|---|---|---|
| time | timestamp | Bar open time |
| open, high, low, close | float64 | Prices in USDT |
| volume | float64 | Volume in the base asset |
| quote_volume | float64 | Volume in USDT |
| trades | int32 | Number of trades |
| taker_buy_volume | float64 | Base-asset volume where the buyer was the taker |
| taker_buy_quote_volume | float64 | Same, in USDT |

Sorted by `time`, with one row group per calendar month. The file has no `symbol` column. The symbol comes from the path, so keep track of it if you read a file directly.

### Candles: 1h and 1d files

Same columns, plus a leading `symbol` (string) column. `trades` is int64. Sorted by `symbol`, then `time`. Bars are aligned to UTC hours and UTC days, matching Binance's own 1h and 1d candles, and are built from the 1m data.

### Funding: funding/data.parquet

| Column | Type | Meaning |
|---|---|---|
| symbol | string | Perpetual contract name |
| time | timestamp | Settlement time, snapped to the minute |
| funding_rate | float64 | Rate for that settlement, as a fraction: 0.0001 is 0.01% |
| interval_hours | int32 | Hours this rate covers. Usually 8, but 4 or 1 on some contracts and periods. Can be null on a symbol's first row. |

Sorted by `symbol`, then `time`. One row per settlement. Delivery contracts have no funding and are not in this file.

A positive rate means longs pay shorts at `time`; negative means shorts pay longs. The payment is position notional (at mark price) times `funding_rate`. Only positions open at the settlement instant pay or receive.

### Metrics: 5m files

| Column | Type | Meaning |
|---|---|---|
| time | timestamp | Snapshot time |
| sum_open_interest | float64 | Open interest in the base asset (contracts) |
| sum_open_interest_value | float64 | Open interest in USD |
| count_toptrader_long_short_ratio | float64 | Top traders' long/short ratio, by number of accounts |
| sum_toptrader_long_short_ratio | float64 | Top traders' long/short ratio, by position size |
| count_long_short_ratio | float64 | Long/short ratio across all accounts |
| sum_taker_long_short_vol_ratio | float64 | Taker buy volume divided by taker sell volume |

Column names are Binance's own. The ratio descriptions follow Binance's published definitions for these series; if a result depends on the exact definition, sanity-check it against the values. Any column can be null in a given row. Sorted by `time`, one row group per month, no `symbol` column (it comes from the path).

### Metrics: 1d file

Same columns with a leading `symbol`. One row per symbol per UTC day, holding the **last** 5m snapshot of that day. `time` is the **start** of the day. Sorted by `symbol`, then `time`.

### Taker flow: taker_flow/interval=1d/data.parquet

One row per symbol per UTC day, built from Binance's aggTrades dumps. One "print" is one aggTrades row: the fills of a single taker order at a single price. Its notional is price × quantity, in USDT, and that notional decides its size bucket:

`lt_100` (< $100) · `100_1k` ($100 to <$1k) · `1k_10k` · `10k_100k` · `100k_1m` · `ge_1m` (≥ $1m)

A print exactly on a boundary goes to the upper bucket.

| Column | Type | Meaning |
|---|---|---|
| symbol | string | Contract name |
| time | timestamp | Start of the UTC day the row aggregates |
| taker_net_usd | float64 | Taker buy − taker sell $, all prints |
| taker_gross_usd | float64 | Taker buy + taker sell $, all prints |
| taker_net_small_usd, taker_gross_small_usd | float64 | Same, prints **under $1k** (`lt_100` + `100_1k`) |
| taker_net_large_usd, taker_gross_large_usd | float64 | Same, prints of **$100k and over** (`100k_1m` + `ge_1m`) |
| buy_usd_&lt;bucket&gt;, sell_usd_&lt;bucket&gt; | float64 | Taker buy / sell $ in that bucket (18 raw columns in all) |
| trades_&lt;bucket&gt; | int64 | Number of prints in that bucket |

"Buy" means the taker was the buyer (aggressive buying); "sell" means the taker was the seller. The six summary columns are exactly sums of the raw bucket columns, so any other split (say, ≥ $10k as "large") can be recomputed from the raw columns without new data. Sorted by `symbol`, then `time`. About 669k rows over 1,049 symbols, from January 2020.

### meta/symbols.parquet (candle coverage)

| Column | Meaning |
|---|---|
| symbol | Contract name, for example `BTCUSDT` or `BTCUSDT_210326` |
| contract_type | `perpetual` or `delivery` (delivery names contain an underscore and an expiry date) |
| first_time | First 1m candle |
| last_time | Last 1m candle |
| rows_1m | Number of 1m candles |
| is_live | True if the symbol has candles in the final 24 hours of the dataset. False means delisted or expired. |

### meta/metrics_symbols.parquet (metrics coverage)

Columns: `symbol`, `first_time`, `last_time`, `rows_5m`. Metrics start later than candles for older contracts (September 2020 at the earliest), so check this file before assuming open interest exists for a period.

### meta/manifest.json

| Key | Meaning |
|---|---|
| data_through | Last candle time in the bucket |
| funding_through | Last funding settlement in the bucket |
| metrics_through | Last metrics snapshot in the bucket |
| flow_through | Last taker-flow day in the bucket (start-of-day stamp) |
| symbols, symbols_live, rows_1m | Candle totals |
| funding_symbols, funding_rows | Funding totals |
| metrics_symbols, metrics_rows_5m | Metrics totals |
| flow_symbols, flow_rows | Taker-flow totals |
| generated_at | When the manifest was last written |

A `*_through` key is absent if that dataset has not been loaded yet.

## Freshness

| Dataset | Typical lag | Why |
|---|---|---|
| Candles | about a day | Loaded from Binance's daily dump files |
| Metrics | about a day | Loaded from Binance's daily dump files |
| Taker flow | about a day | Loaded from Binance's daily dump files |
| Funding | a few hours | Pulled from the Binance API several times a day |

Always read the manifest rather than assuming. The three datasets end at different times, so clip to the earliest `*_through` when combining them.

## Recommended workflow

1. Read `meta/manifest.json` and note the `*_through` times.
2. Read `meta/symbols.parquet` (and `metrics_symbols.parquet` if you need metrics) to decide which symbols and years you need. They are tiny. Taker flow has no coverage meta file; get per-symbol coverage from the data file itself — it is small enough to query directly.
3. Build explicit file paths from that, and read only the columns you need.
4. Process large 1m or 5m pulls one symbol at a time.

## Reading the data

### DuckDB (preferred)

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute(f"CREATE SECRET (TYPE s3, KEY_ID '{key}', SECRET '{secret}', REGION 'eu-west-2')")
con.execute("SET TimeZone = 'UTC'")

ROOT = "s3://binance-data-546016014952-eu-west-2-an/v1/binance/um"

# What exists
symbols = con.execute(f"SELECT * FROM read_parquet('{ROOT}/meta/symbols.parquet')").df()

# Daily bars for the whole universe
daily = con.execute(f"""
    SELECT symbol, time, close, quote_volume
    FROM read_parquet('{ROOT}/klines/interval=1d/data.parquet')
    WHERE time >= '2024-01-01'
""").df()

# Hourly bars for some symbols in one year
hourly = con.execute(f"""
    SELECT symbol, time, open, high, low, close, volume
    FROM read_parquet('{ROOT}/klines/interval=1h/year=2025/data.parquet')
    WHERE symbol IN ('BTCUSDT', 'ETHUSDT')
""").df()

# 1m bars for one symbol across years
minute = con.execute(f"""
    SELECT time, open, high, low, close, volume
    FROM read_parquet('{ROOT}/klines/interval=1m/symbol=BTCUSDT/year=*/data.parquet',
                      hive_partitioning = true)
    WHERE year >= 2024 AND time >= '2024-06-01'
""").df()

# 1m bars for several symbols: pass explicit paths, and take symbol from the path
paths = [f"{ROOT}/klines/interval=1m/symbol={s}/year={y}/data.parquet"
         for s in ("BTCUSDT", "ETHUSDT") for y in (2024, 2025)]
multi = con.execute("""
    SELECT symbol, time, close
    FROM read_parquet(?, hive_partitioning = true)
""", [paths]).df()

# Funding history for some symbols
funding = con.execute(f"""
    SELECT symbol, time, funding_rate, interval_hours
    FROM read_parquet('{ROOT}/funding/data.parquet')
    WHERE symbol IN ('BTCUSDT', 'ETHUSDT') AND time >= '2024-01-01'
""").df()

# Annualised funding per symbol over the last 30 days of data (respects 8h, 4h and 1h intervals)
carry = con.execute(f"""
    WITH f AS (SELECT * FROM read_parquet('{ROOT}/funding/data.parquet'))
    SELECT symbol, sum(funding_rate) * 365 / 30 AS annualised_rate, count(*) AS settlements
    FROM f
    WHERE time > (SELECT max(time) FROM f) - INTERVAL 30 DAY
    GROUP BY symbol ORDER BY annualised_rate DESC
""").df()

# Daily open interest for the whole universe
oi_daily = con.execute(f"""
    SELECT symbol, time, sum_open_interest_value
    FROM read_parquet('{ROOT}/metrics/interval=1d/data.parquet')
    WHERE time >= '2025-01-01'
""").df()

# 5-minute open interest for one symbol
oi_5m = con.execute(f"""
    SELECT time, sum_open_interest, sum_open_interest_value
    FROM read_parquet('{ROOT}/metrics/interval=5m/symbol=BTCUSDT/year=2025/data.parquet')
""").df()

# Daily taker-flow imbalance, with the large-print share of gross
flow = con.execute(f"""
    SELECT symbol, time,
           taker_net_usd / NULLIF(taker_gross_usd, 0)              AS imbalance,
           taker_net_small_usd / NULLIF(taker_gross_small_usd, 0)  AS small_imbalance,
           taker_gross_large_usd / NULLIF(taker_gross_usd, 0)      AS large_share
    FROM read_parquet('{ROOT}/taker_flow/interval=1d/data.parquet')
    WHERE time >= '2025-01-01'
""").df()

# Attach to each hourly bar the latest funding rate known at that time
joined = con.execute(f"""
    SELECT k.time, k.close, f.funding_rate, f.time AS funding_time
    FROM read_parquet('{ROOT}/klines/interval=1h/year=2025/data.parquet') k
    ASOF LEFT JOIN read_parquet('{ROOT}/funding/data.parquet') f
      ON k.symbol = f.symbol AND k.time >= f.time
    WHERE k.symbol = 'BTCUSDT'
""").df()
```

Filter on `year` as well as `time` so whole files are skipped.

### Polars

```python
import polars as pl
opts = {"aws_access_key_id": key, "aws_secret_access_key": secret, "aws_region": "eu-west-2"}
ROOT = "s3://binance-data-546016014952-eu-west-2-an/v1/binance/um"

df = (pl.scan_parquet(f"{ROOT}/klines/interval=1m/symbol=BTCUSDT/year=2025/data.parquet",
                      storage_options=opts)
        .select("time", "close", "volume")
        .collect())

funding = (pl.scan_parquet(f"{ROOT}/funding/data.parquet", storage_options=opts)
             .filter(pl.col("symbol") == "BTCUSDT")
             .collect())
```

### Plain download

```python
import boto3
s3 = boto3.client("s3", aws_access_key_id=key, aws_secret_access_key=secret, region_name="eu-west-2")
s3.download_file("binance-data-546016014952-eu-west-2-an",
                 "v1/binance/um/funding/data.parquet", "funding.parquet")
```

## Pitfalls

General:
- **Survivorship bias.** Many contracts have been delisted. When building a universe for a past date, use `first_time` and `last_time` from `symbols.parquet` to include every contract that was trading then, not only those with `is_live = true`.
- **Perpetuals versus delivery contracts.** Filter `contract_type = 'perpetual'` unless you specifically want dated futures. Delivery contracts expire, each quarter gets a new symbol, and they have no funding.
- **A symbol does not have every year, or every dataset.** A file exists only for years in which the symbol has data. Check the meta files before building paths, or expect missing-file errors.
- **Non-ASCII symbol names.** A few contracts have Chinese names, for example `币安人生USDT`. Pass them to S3 tools as they are and let the library handle encoding. Do not hand-build HTTP URLs.
- **The datasets end at different times.** Check the manifest, and never treat the last row as the current value.
- **Current-year files are rewritten on each update.** Files for past years never change and are safe to cache. Re-fetch current-year files, the funding file and the daily files rather than reusing a copy from an earlier day.
- **Do not pull everything.** All 1m candles are tens of GB and over a billion rows. They will not fit in memory, and every download has a transfer cost. Select columns, restrict years, and iterate symbol by symbol.
- **Read-only.** You cannot write to this bucket. Save derived data and results elsewhere.

Candles:
- **Missing minutes.** There is no forward filling. Minutes missing from Binance's own data (exchange outages, halts) are absent here too. Do not assume 1,440 rows per day. Reindex explicitly if your logic needs a regular grid.
- **Time is bar open time.** A 1m bar stamped 12:00 covers 12:00:00 to 12:00:59. A 1d bar stamped 2025-03-01 covers that whole UTC day. Do not use a bar's close before the bar has ended, or the backtest will look ahead.

Funding:
- **Do not assume 8 hours.** Use `interval_hours`, or simply sum `funding_rate` over the window, when annualising or comparing symbols. Intervals differ between contracts and have changed over time.
- **Funding is paid at an instant.** A position opened just after a settlement and closed just before the next pays nothing. Apply each rate only to positions held at its `time`.
- **Rates are extreme at times.** Values far from zero are real, not errors, particularly around squeezes and on small contracts.

Taker flow:
- **A row looks ahead if misused.** A row stamped 2025-03-01 aggregates that whole UTC day, so it is only known after the day closes. Pair it with the next day's decisions, exactly as you would a daily close.
- **Same-day flow and same-day returns are nearly the same thing.** Net taker flow and the day's return are mechanically correlated; a "signal" built from day T flow predicting day T returns is look-ahead, not alpha.
- **Print size is not order size.** A large order split into many fills at different prices (or sliced by an algo) shows up as several smaller prints, so the large buckets undercount true large-order activity and the small buckets contain slices of big orders, not only retail.
- **Buy and sell are taker-side labels.** Every trade has a buyer and a seller; these columns say which side was the aggressor. Buys and sells here never cancel against each other except in the `net` columns.
- **Missing rows and zero rows differ.** A row of zeros means the day was loaded and had no trades. A missing (symbol, day) row means the day was not loaded — treat it as a gap, not as zero flow.

Metrics:
- **The daily file looks ahead if misused.** A row stamped 2025-03-01 holds the snapshot from the end of that day. It is only known after the day closes, so pair it with the next day's decisions, exactly as you would a daily close.
- **A 5m snapshot is known at its timestamp, not before.** Join metrics to candles with an as-of join on `time`, never by rounding forward.
- **Ratios are not counts.** The long/short columns are ratios Binance computes. A value above 1 means more long than short by that measure. They cannot be converted back into position sizes.
- **Coverage starts later than candles.** Nothing before September 2020, and nulls and gaps are more common than in the candle data.
