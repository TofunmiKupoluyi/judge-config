# RISK FRAMEWORK — the production risk model (binding on every proposal)

Current as of 2026-10-10, including the lessons of the 2026-10-09 post-trade review. The machine-readable twin is `reference/RISK_FRAMEWORK.json`; the two must agree, and neither is edited by an agent.

Everything is measured on **notional** (the program's capital number, in an external config, never margin or equity). The model is computed at a $10M reference book and scaled to notional; weights below are fractions of NAV = notional × compounded NAV index.

## 0. Why this shape (one paragraph)
The program's hard stop is a 10% peak-to-trough drawdown from the high-water mark, on notional, with mandatory gross cuts before 6% and 8%. The strategy is therefore built outward from that constraint: reward-sized positions capped by liquidity and stress loss, a fund-level BTC hedge that keeps beta and delta inside ±10% of NAV, and a continuous governor that scales the whole book by distance-to-stop ÷ (k × realised unscaled vol) so the book is small when it is volatile or wounded and never fully flat (a flat book freezes its drawdown and can never recover). Every number below has a research id behind it and a reason not to be "optimised".

## 1. Program constraints (Nickel DAL — external, non-negotiable)
- **Hard stop: 10% DD from HWM on notional.** Breach → capital cut to $100k or lower, immediate-termination right.
- **HWM mechanics:** HWM resets only once exceeded by 2% (60-minute moving average of P&L); trough tracked in real time; momentary bad prints disregarded; verifiable through the risk API.
- **Mandatory de-risk ladder:** before 6% DD gross < 50% of the agreed gross limit (restore to 100% only below 4%); before 8% DD gross < 25% (restore to 50% only below 6%). If the manager does not cut, the program closes ALL positions.
- **Indicative exposure caps:** 200% gross, ±30% net (we run far inside: see §4).
- **Scaling:** $100k test notional → ~doubling every two weeks once statistical confidence exists (4–6 weeks) → … → $5M increments; pauses on rolling 4-week Sortino < 1.5, PTT drawdown > 5%, or slippage / fill-ratio / compliance concerns. ≥ 1 day notice of notional changes.
- **Monitoring expectations:** real-time (not EOD) P&L-from-HWM and exposure; modular RMS on a separate server with heartbeats, auto-restart, fail-safe de-risk on module failure; kill switches at strategy and portfolio level.

## 2. Universe
- Binance USDT-M perpetuals, underlying type COIN only (indices and non-crypto underlyings excluded); BTCUSDT is the hedge leg, never a signal name.
- Hysteresis membership on 21-day median daily quote volume (MDV21): **enter at ≥ $3M, stay while ≥ $2M**.
- Delisting ramp (announcement-lead proxy, disclosed): no new entries in the last 14 days of a symbol's data; membership ends 7 days before the data ends; a name leaving the universe is closed on the next cycle.
- Typical size: 170–300 names in; ~160–200 held.

## 3. Sizing (reward sizing; `size_targets`)
For each name with edge e (bp, the alpha's expected residual return) and MDV21 m ($):
raw_$ = sign(e) · max(|e| − γ, 0) · 1e-4 · √m · reward_scale, with **γ = 5 bp** (gamma hurdle), **reward_scale = 40,000** (calibrated so calib-window average GMV ≈ 3× AUM at $10M, i.e. a √ADV inventory penalty), target weight = raw_$ / AUM, zero outside the universe. In production (v2.3) the target is the mean of seven phase books, each phase re-ranked on its weekday; the sizing formula is unchanged.
LiquidityTilt: the √MDV term is itself a BTC-vs-large-alt tilt (E79) — it is part of the alpha, not a risk parameter.

## 4. Per-name caps (applied in this order; all in NAV weight)
1. **Liquidity leg:** min(20% of open interest $, 30% of MDV21, 15% of market cap), floored at **0.1% of AUM**; the market-cap leg is inert where mcap is missing; a zero OI print is treated as missing (data gap), never as a cap of zero.
2. **Concentration:** ≤ **5% of NAV** per name.
3. **Max-loss (stress) caps, asymmetric by side and MDV tier** (Large ≥ $100M, Medium $10–100M, Small < $10M MDV): a position may not lose more than **5% of AUM** in its stress move. Short-squeeze stress **+200% / +300% / +500%** → short caps **2.5% / 1.67% / 1.0%** of NAV. Long-collapse stress **−50% / −65% / −80%** → long caps 10% / 7.7% / 6.25% (non-binding behind the 5% concentration cap: the long book is capped by concentration, the short book by squeeze stress). Adopted as v2.1 after the stress-episode review (E61/E61b), disclosed as post-hoc hardening.
4. **GMV cap: 4× AUM** at the target level (pre-governor; typical realised gross ≈ 0.9× at the master's governor level, 1.5–1.7× unscaled).
5. Per-symbol exchange leverage setting **10×** (initial-margin knob only, `LEVERAGE_CAP`; it is not a risk limit).

## 5. Fund-level limits and the BTC hedge
- **|beta$| ≤ 10% of NAV** (rolling 60-day hourly beta to BTC, clipped [−3, 3], lagged) and **|delta$| ≤ 10% of NAV** (net notional), both solved jointly each hour by ONE instrument, the BTC perp: hedge = clip(−B, max(−B − 0.10, −D − 0.10), min(−B + 0.10, −D + 0.10)). If the interval is empty (|B − D| > 20% of NAV) the alt book is scaled down by (0.20 / |B − D|) first — targets are projected into the feasible set before trading (2026-07-22 fix: scaling held positions caused a rebuy/scale-down oscillation).
- **Hedge band 2%**: the BTC position is re-set only when the required change exceeds 2% of NAV, so |beta$| and |delta$| run to 1.2× the cap by design (backtest: > cap 37% of hours for delta, never > 1.2×). Monitoring: warn from 1.0×, breach/alert only beyond **1.3×** — a breach means the hedge is not working (BTC order failing, large gaps), not that the model is wrong.
- BTCUSDT is measured against the **hedge envelope** |model BTC| + max(|alt beta$|, |alt delta$|) + 10% N, never against per-name caps.
- Hedge leg share of the book: 28–37% of net in the backtest; realised live median 55% of alt gross (the backtest's 58%).
- Realised BTC beta of the live hourly P&L: −0.03 ± 0.02 of NAV per unit BTC return — the hedge does its job.

## 6. Drawdown governor — production = CPPI profile (`risk_profile: nickel`, engine `dd_cppi`)
- **Formula, evaluated every hour on compounded NAV:** mult = clip((0.095 − dd) / (10 × σ_u), 0.05, 1.00), where dd = 1 − NAV/HWM and **σ_u** is the EWMA (span 10 days, λ = 2/11) of the UNSCALED book's daily return = realised daily NAV return ÷ max(multiplier, 0.05); seed 1.5%/day (re-seeded at a master switch to the new book's expected unscaled vol — v2.3: 2.754%).
- **cppi_stop 9.5%** (0.5 pt inside the 10% hard stop), **k = 10**, **floor 5%** (never fully flat).
- **DAL clamp as a hard envelope:** mult ≤ 0.50 once dd ≥ 5.5%; mult ≤ 0.25 once dd ≥ 7.5% (triggers 0.5 pt inside the program's "before 6% / 8%" so confirmed cuts complete in time). Restores are implicit in the formula (no hysteresis dead zones: continuous and instantly reversible).
- **De-risk trades bypass the no-trade band and the 4-hour resize gate** whenever mult < 1 and the trade reduces |position| without changing sign.
- Governor moves are logged as `ladder` events with dd, σ_u, mult_from/to; expect ≈ 4–5 moves/day live (22/day in the hourly backtest, where every hour can move).
- Alternatives kept in the engine, default off: **Step ladder** (master v2.2: gross → 50% at dd 5.5%, → 25% at 7.5%, restore 100% < 4%, 50% < 6%, instant — dwell smoothing tested and worse, E66); glide path; third rung (soft stop 10%); per-name cumulative-loss cooldown (close + freeze a name after burning 5% of NAV in 30 days; tested, not adopted).
- **Why CPPI:** vol-anticipatory AND depth-aware; E68 backtest 2022–26 +23.4%/yr, Sharpe 1.78, max DD −8.6% hourly, zero hours beyond −10%. The program's scaling is gated by the 4-week Sortino, so an "incubation" profile with a tighter line was rejected (E101).

### 6a. The live lesson (2026-10-09 post-trade review) — the governor's NAV basis
The governor reads the ACCOUNT NAV. Discretionary operator holds placed inside the same account (ten quantity holds, $17–19k = 17–19% of notional, all long, MDV $2–50M) are not scaled by the multiplier but their P&L enters dd and σ_u. Result: hold losses carried dd from ≈ 3.5% to 7.2% across the 6% line; once mult ≈ 0.09 one −1.4% hold day ÷ 0.09 doubled σ_u from 4.5% to 8.9% (backtest 2.8%) and parked the multiplier at the 5% floor — a one-way ratchet, since every further hold swing ÷ 0.05 keeps σ_u high. Replayed on the systematic book alone the governor reads σ_u 2.0–2.8% and mult 0.18–0.42. **Rule for any deployment that supports discretionary or multi-strategy positions in one account: the governor's dd and σ_u must be computed on the governed book's own P&L (or each book must have its own account); the pre-registered rollback rule must name which NAV it uses before any live data exists.**

## 7. Trade gating (why turnover is 0.05–0.07 × NAV/day, not hourly churn)
- **No-trade band:** a resize is skipped if |Δw| < 50% of |target| or < 0.1% of NAV (10 bp).
- **Resize cadence:** existing positions resize only every 4 hours (`adjust_every_h`); opens, closes and governor de-risk trades are allowed every hour.
- **Entry hurdle:** a new position opens only if |edge| × expected_hold (5 days) − 2 × (half-spread tier + fee) ≥ γ (5 bp), and never while the name is in its delisting ramp.
- **Universe exit:** a name leaving the universe is closed; closes and trims are reduce-only.
- Backtest trade mix (E120): governor micro-trims are 93% of trade records and 44–46% of turnover; live: 59% of fills, 35% of traded $.

## 8. Execution (participation-capped, sliced MARKET orders; `execute()`)
- Per name and cycle: at most **5% of the recent hour's quote volume** (`participation_cap`); the remainder is DEFERRED to the next cycle (recorded, Telegram-listed, not a coverage gap).
- The traded amount is cut into up to **10 MARKET slices 2 minutes apart**, each ≤ 0.5% of hourly volume; small orders (the $100k book) are a single immediate slice. Trims/closes reduce-only; a capped close becomes a reduce-only trim.
- Cycle budget: service timeout 3,480 s; reserve 840 s + the 20-minute slice window; a venue ban that lifts inside the window is waited out, otherwise the cycle halts (exit 2 = "cycle skipped", not a unit failure).
- Capacity language: $1–10M supported, $10–25M execution-dependent, $25M+ not validated (deferred-order audit: the capacity curve bends at $50M through the participation cap).

## 9. Cost model (backtest; `RISK2`) and what was measured live
- **Taker fee 4 bp; half-spread by MDV tier 1 / 3 / 8 bp (≥ $100M / $20–100M / < $20M); impact 15 bp × √(trade $ ÷ MDV21)**; the hedge pays fee + 1 bp; funding per print; costs charged on every executed trade including governor trims and hedge re-sets.
- **Gauntlet:** every master change and every candidate must survive 2× every cost term; the v2.3 master loses ≈ 0.3 Sharpe at 2×.
- **Measured live (28 days, 1,235 fills, $305k; 2026-10-09):** fee 2.75 bp; slippage vs the bar close +0.4 bp against 4.4 modelled; all-in 3.1 vs 8.4 bp; annualised drag 1.24% of N vs 1.7–2.2% in the backtest; OPEN −14 bp (favourable), CLOSE +23 bp, TRIM +2, HEDGE 0; impact 1.8 bp at < 0.5% participation; 100% taker. The model stays as the conservative default; the measured stack is a disclosed sensitivity.

## 10. Monitoring thresholds (limit rows written every cycle and every 5 minutes)
| limit | warn | breach | note |
|---|---|---|---|
| beta$ / 10% N | 1.0× | 1.3× | model sits on the cap; band allows 1.2× |
| delta$ / 10% N | 1.0× | 1.3× | same; operator holds anchored at target push delta, not beta |
| GMV / 4× N | 1.0× | 1.5× | typical 0.9× |
| leverage (GMV / equity) | 3.0 | — | internal |
| margin ratio (maint / equity) | 0.5 | 0.8 | exchange liquidates at 1.0 |
| dd vs 5.5% (ladder 1), 7.5% (ladder 2) | 1.0× | 1.5× | ours |
| dd vs 6% / 8% / 10% (DAL lines, hard stop) | 1.0× | 1.5× | program |
| BTC parallel shift to liquidation | beyond −30% down / +50% up | | |
| per-position vs min(liquidity, 5% NAV, max-loss) | 1.0× | 1.5× for two consecutive cycles | one message per symbol |
| coverage (held ÷ intended gross) | < 80% | | |
Alert policy: one event → one message; sound only for strategy breaches (DD lines, coverage < 80%, beta/delta > 1.3×, leverage/margin/shift, position > 1.5× for two cycles) or services down (trader silent 90 min, collector 30 min, crash, external dead-man's switch). Hourly reports silent.

## 11. Operator control plane (`control.json`, read at the start of every cycle; absent = bit-identical cycle)
`paused` (no orders), `reduce_only` (closes and trims only), `gross_cap` (multiplier on the model's gross, ≤ 1), `blocklist` (names the model may not hold), `targets` (operator quantity holds: dollars converted to a fixed quantity at entry, capped at entry, alpha ignored, part of the book for hedge/margin/NAV, not scaled by the governor; hedged at their target). Every change is a hash-chained record and a Telegram line. No flatten button (typed command). Master parameters are never editable from the control plane. See §6a for the interaction with the governor.

## 12. Accounting conventions the risk numbers depend on
- Exchange equity is the source of truth; equity = Σ collateral assets (USDT, USCX) + unrealised; NAV = 1 + cumulative P&L ÷ notional; P&L = realised + funding + commission + Δunrealised + "other" (wallet residual below 0.1% of notional); flows (deposits/withdrawals) are not P&L; on Portfolio Margin, UM↔margin TRANSFER rows that leave the wallet unchanged are **internal**, not flows.
- HWM and dd are measured inside the current venue's baseline segment only; restatements re-derive from definitions and are idempotent.
- Ledger tie-out each cycle: Σ income rows + Δunrealised + flows = Δequity within $2 (within $5 across a window); a missing cycle record is bookkeeping, not P&L.

## 13. Research adoption bar and rollback rule (risk-relevant governance)
- **Adoption bar** (any sleeve, veto, sizing or governor change): paired Δ ≥ 0 in BOTH windows at the master's risk budget (governor 2×2, imposed path), eval NW t ≥ 1.5, survives 2× costs, no year more than 3 points worse; then shadow/paper, then small capital. A production change bumps the version in engine and trader in lockstep, reproduces the old master bit-exactly first, runs the gauntlet, and is tagged.
- **Pre-registered rollback rule for a new master (40 live days):** rolled back if paired net P&L vs the exact previous-master shadow ≤ −1.5% of notional AND one of: mean paired daily difference ≤ −3 bp/day or cumulative paired difference touches −2.0%; realised turnover > 0.075 × notional/day over four consecutive weeks; implementation shortfall > model + 2 bp or > 130% of model over four weeks; DD exceeds the shadow's by > 2 points or crosses 5.5% while the shadow stays below 4%. Economic thresholds, no significance test. (Live lesson: state the NAV basis — see §6a.)
- **Reading of an untouched window:** A = alpha at the previous master's risk path, G = governor interaction, Δ = A + G, with pre-registered cells (consistent with calibration edge A ≥ +1.0 and Δ ≥ +1.0; zero edge −0.5 ≤ A ≤ +0.5; evidence against A ≤ −1.0; strong evidence against the deployment Δ ≤ −1.5; grey zones stay grey). A is the critical number.

## 14. Standing numbers a proposal must respect (for the Scout)
A name below $2M MDV is outside the universe; a short in a Small-tier name is capped at 1% of NAV; a long at 5%; the whole book at 4× (pre-governor) and in practice 1.5–1.7× unscaled; net and beta within ±10%; nothing trades more than 5% of an hour's volume; a signal that needs more than 5% of NAV in one name, more than 20% of a name's OI, or a hold shorter than the 4-hour resize cadence does not fit the risk model and is classified as an execution or microstructure study, not a sleeve.
