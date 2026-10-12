# The production strategy — names for humans (2026-09-11)

Code identifiers (`a4`, `a12`, `a90x_ratio`, `PROFILES["nickel"]`, `dd_cppi`, …) stay as they are — renaming production symbols the night before a deployment is not worth the regression risk, and the research ledger refers to them. In prose, reports, Telegram and dashboards we use the nicknames below, code id in brackets the first time: e.g. "FundingCarry (A4)".

## Risk governors (how the whole book is scaled with drawdown)

| nickname | code | what it does |
|---|---|---|
| **CPPI governor** ("Constant Proportion Portfolio Insurance", a 1980s portfolio-insurance rule; the user prefers the original name) | `risk_profile: nickel`, engine `dd_cppi`, `PROFILES["nickel"]` | Size = **cushion ÷ (10 × daily vol)**: the cushion is the distance from the current drawdown to the 9.5% line (just inside Nickel's 10% stop); the vol is the 10-day EWMA of the full-size book's daily returns. The book is kept small enough that ten one-day standard deviations fit in the cushion. Rises in vol or losses shrink it smoothly and continuously; recoveries restore it; floor 5%; the DAL 50% / 25% steps at 5.5% / 7.5% are kept as hard clamps. In prose: "the CPPI governor" or "CPPI profile". **This is what runs in production.** |
| **Step ladder** | `dd_ladder` (master v2.2 default), `PROFILES["master"]` | Full size until 5.5% drawdown, 50% until 7.5%, 25% beyond; restored to 50% below 6% and to full below 4%. Reacts to losses only, in steps. Master's own rule; not used live under the Nickel profile. |
| **Soft stop** (research only) | `dd_cut3 / dd_mult3` | Optional third rung to 10% size — tested, not adopted. |

## Alphas (sleeves) — master and candidate

| nickname | code | one-liner | status |
|---|---|---|---|
| **FundingCarry** (A4) | `a4`, `alphas.a4_funding_carry` | Get paid the funding rate: long the names with the most negative 3-day average funding (shorts pay you), short the most positive (longs pay you), only when the 30-day price trend agrees; edge = 0.35 × daily funding in bp. Trades alone only in extreme funding (> ~52%/yr); mostly a size tilt on IlliquidityPremium positions. | live (v2.2 → v2.3) |
| **IlliquidityPremium** (A12) — "ThinTape" | `a12`, `staged_v2.stage_a12` | Long the 20% of names where a dollar of volume moves price the most (Amihud, 21 days), short the 20% where it moves it least; ranks frozen weekly, stay-in bands 80/70; ±8 bp. In practice: long thin small caps, short the liquid large caps, BTC-hedged. The core of the book. | live (v2.2 → v2.3) |
| **TakerFlow** (A90) — "Aggressor" | `a90w` (fixed ±8), `alphas.a90_pvi7` | Who has been hitting the tape for a week: taker-buy minus taker-sell quote volume over 21 × 8h blocks, weekly-frozen ranks, top/bottom quintile with hysteresis; long net aggressive buying, short net aggressive selling. Fixed ±8 bp = the v2.3 dossier candidate (not adopted). | candidate, superseded |
| **TakerFlow-R** (A90 ratio) — "Conviction" | `a90x_ratio`, `alphas.a90_pvi7_scaled(mode="ratio")`, live `a90_refresh()` | Same selection as TakerFlow, but the edge scales with how one-sided the flow was: 8 bp × (|buy − sell| / |buy + sell|, relative to the tail median), clipped 4–16 bp. Mildly one-sided large caps fall below the 5 bp hurdle and drop out. | **v2.3 — deploying 2026-09-11 (inside the seven-phase ensemble)** |
| **Seven-phase ensemble** (E98/E98b) | live `phases` state, `refresh_phase()` / `ensemble_raw()`; backtest `ens7_both` panels via `V2_EXTRA` | Each weekly sleeve (IlliquidityPremium, TakerFlow-R) is kept in seven copies, one re-ranked per weekday; the book is the average of the seven phase books, so nothing depends on which weekday the lists are rebuilt. v2.3 construction. |
| **BTCHedge** | engine hedge block, live `hedge_w` | Fund-level BTCUSDT position that keeps the book's net beta-$ and net delta-$ inside ±10% of NAV (2% band). Not an alpha, but it earns/loses a lot: the book is structurally long BTC against short liquid alts. | live |

## Research alphas (not in master; verdicts are on the ledger — the hypothesis issues in Linear)

| nickname | code | one-liner | verdict |
|---|---|---|---|
| XSecReversal (A1) | `a1_xsec_reversal` | Cross-sectional reversal on beta-stripped returns. | pre-master, dropped |
| RelStrength (A2, v2/v3) | `a2_*` | Relative strength vs the majors basket, gradual sizing. | v2.0-era, dropped |
| Ignition (A3) | `a3_ignition` | Volume/taker ignition breakouts. | dropped |
| OIMomentum (A5) | `a5_oi_momentum` | Momentum confirmed by open-interest build. | dropped |
| Donchian (A6) | `a6_donchian` | 20/10-day channel breakout (Turtle). | dropped |
| RiskOff (A7) | `a7_risk_off` | Flight-to-quality overlay: short everything when stress spikes. | dropped |
| SizeFactor (A13) | `stage_a13` | Long small-MDV, short large-MDV (0.82 P&L correlation with IlliquidityPremium). | not adopted |
| PeerMomentum (A89) | `research_e89` | Momentum of price-derived peer groups. | rejected E89 |
| Skew (A91) | `research_e91` | 56-day return skewness. | rejected E91 |
| FlowVeto (E94) | `scale_e94` | IlliquidityPremium shorts cancelled when TakerFlow is positive. | rejected E94 |
| AltDominance overlay (E75), ETHResidualHedge (E77), ResidualAmihud (E82), BetaBalanced (E83), FlatSizing (E79b), Core/Tilt (E88) | see the ledger (Linear) | Structural fixes to the long-BTC / short-large-alt exposure. | all rejected — the exposure is the short leg's alpha |

## Sizing vocabulary

| term | meaning |
|---|---|
| **edge (bp/day)** | expected excess return per day per name; the sleeves' outputs are summed (weight 1.0 each) |
| **hurdle** (`gamma_bps` = 5) | the first 5 bp of |edge| earn no position; size ∝ (|edge| − 5)⁺ |
| **LiquidityTilt** | the √MDV21 factor in sizing: more dollars to more liquid names at the same edge (E79/E88: this is the long-BTC / short-large-alt "tilt"; the balanced part of IlliquidityPremium is the "core") |
| **caps** | per name: min(20% OI, 30% MDV21, 15% mcap, 5% NAV, max-loss tier — shorts 2.5 / 1.67 / 1.0% NAV by liquidity tier); book: GMV ≤ 4× NAV, beta/delta projection (whole book shrunk when one BTC hedge cannot neutralise both) |
| **governor multiplier** | the CPPI governor's (or ladder's) scale factor on the whole book, 5%–100% |
| **unscaled book** | the targets before the governor multiplier (what the governor's vol estimate refers to) |
| **Monday refresh** | live: weekly-frozen ranks (IlliquidityPremium, TakerFlow) are recomputed on the first cycle of the ISO week; the backtest grid refreshes on Wednesdays — the E95b offset table measures the difference |
