# Research principles (Dominik, 2026-09-15) — the standing framework for all alpha research, backtesting, portfolio construction and production changes

*Saved verbatim at Dominik's request; the default guideline for every research discussion unless he explicitly says otherwise. Summary also in Claude's project memory. Read this before any research work.*

## 1. Overall objective
My goal is not to maximize historical Sharpe or find isolated backtests that look good. The objective is to build a robust hedge-fund-style research process that repeatedly discovers genuinely predictive, economically explainable, implementable, and preferably uncorrelated alphas that improve the production portfolio after realistic costs.

Think of the process as: market observation → economic hypothesis → signal definition → predictive testing → backtest → robustness testing → portfolio interaction → execution analysis → paper/shadow deployment → production deployment → live monitoring.

The standard should always be: "Would I be comfortable allocating meaningful real capital to this signal?" Be skeptical by default. Try to falsify hypotheses rather than prove them.

## 2. Production strategy discipline
Treat the production strategy as protected. Never change production logic, production configuration, master strategy files, or parameters merely because a research experiment performs well. Unless I explicitly authorize otherwise: research should happen independently from production; production should remain untouched; research variants should be clearly identifiable; experiments should be reproducible; every meaningful experiment should have a recorded hypothesis and result; rejected ideas should remain documented rather than silently disappearing.

A research result should progress approximately through: idea → research experiment → robustness testing → candidate → shadow/paper strategy → small allocation → production. Do not jump directly from a promising backtest into production.

## 3. Core philosophy for alpha research
The most important question is not "Does this increase Sharpe?" but "Why should this information predict future returns, and is that relationship likely to persist?" Every alpha hypothesis should ideally identify: (1) the economic or market mechanism; (2) who is creating the opportunity; (3) why that participant is behaving predictably; (4) what observable variable measures the phenomenon; (5) what return direction should follow; (6) the expected prediction horizon; (7) how quickly the alpha should decay; (8) which market regimes should strengthen/weaken it; (9) why competition has not fully arbitraged it away; (10) what evidence would invalidate the hypothesis.

Avoid indicator mining without an underlying mechanism. Instead of "Try RSI 17, 21, 24 and 31", prefer "After forced deleveraging, selling pressure may temporarily exceed fundamental demand, creating short-term mean reversion. What data can identify forced deleveraging?"

## 4. Good sources of alpha ideas
**Structural / forced flows** — participants who have to transact regardless of price: liquidations; deleveraging; margin calls; index/rebalance flows; funding mechanics; futures expiry; option hedging; dealer gamma; ETF creations/redemptions; token unlocks; staking/unstaking; treasury movements; collateral changes; exchange-specific mechanics. Ask "Who is forced to trade?" — often one of the best sources of persistent alpha.

**Positioning / leverage** — open interest; funding; basis; liquidations; options positioning; crowded longs/shorts; leverage build-up; leverage unwind. Distinguish price movement caused by genuine information from price movement caused by leveraged positioning.

**Behavioral effects** — momentum; underreaction; overreaction; attention; anchoring; panic; FOMO; disposition effects; post-event drift.

**Market microstructure** — order-book imbalance; aggressor flow; spread changes; liquidity withdrawal; trade imbalance; cross-venue dislocations; temporary price impact; lead/lag relationships.

**Carry / risk premia** — funding; futures basis; volatility risk premium; term structure; borrow; staking yield. Always distinguish genuine predictive alpha from simply taking compensated risk.

**Relative value** — prefer relative predictions (A outperforming B) over directional ones: cross-sectional momentum; relative positioning; pairs; statistical arbitrage; dispersion; cross-exchange relative value; capital-structure relationships.

**Cross-market information** — whether one market leads another: options → spot, futures → spot, BTC → altcoins, equities → crypto, rates → risk assets, on-chain → exchange markets, etc.

## 5. Research the existing strategy's failures
Do not only search for completely new standalone strategies. Study where the current strategy loses money. Perform detailed P&L attribution by: long vs short; asset; asset category; signal type; holding period; volatility regime; trend regime; liquidity regime; market beta; funding environment; open-interest environment; bull/bear/sideways; winner vs loser trades; entry signal strength; time since entry. Find clusters of bad trades, then ask: "What distinguishes these losing trades from successful trades using information that was available at entry?" This can produce filters, veto signals, sizing signals, regime models, exit improvements, independent new alphas. Be extremely careful not to overfit filters specifically to historical losers; every proposed filter must be validated independently.

## 6. Hypothesis before backtest
For every meaningful experiment first write: **Hypothesis** — when X happens, Y should occur over horizon Z because mechanism M causes participants P to behave in way Q. **Expected direction.** **Expected horizon** (e.g. 12h–72h). **Expected regimes** (where strongest). **Invalidation** (what result rejects the theory). Only then run the experiment. This prevents post-hoc storytelling.

## 7. Research signals before building strategies
Before converting a feature into a trading system, investigate its predictive properties directly over multiple horizons (1h, 4h, 12h, 24h, 3d, 7d …): information coefficient; rank IC; conditional expected returns; signal quantiles; long-short spread; monotonicity; hit rate; confidence intervals. Study alpha decay (signal at t → returns at t+1h, +4h, +12h, +24h, +3d): when the effect starts, peaks, disappears, reverses. Do not choose the holding period because one backtested value maximizes Sharpe — it should correspond to the observed economic alpha decay.

## 8. Quantile analysis
Bucket continuous signals into quantiles/deciles and compute future returns. Prefer a sensible relationship (Q1 negative … Q5 strongly positive). A smooth or economically understandable shape is more convincing than a single isolated threshold; if only one tiny signal range works, investigate overfitting.

## 9. Start simple
The first implementation should be the simplest reasonable version (position = z-score(signal), or long strongest quantile / short weakest). Do not immediately introduce many thresholds, multiple interacting filters, unusual exits, complicated state machines, large parameter sets. First establish that the effect exists; complexity must earn its place with robust incremental value.

## 10. Data integrity
Be extremely skeptical of data. Check for look-ahead bias (only information available at the trade timestamp), survivorship bias (universes must include assets that later disappeared), timestamp accuracy (event / publication / exchange / ingestion timestamps), point-in-time correctness (no revised information). Crypto-specific: symbol changes; redenominations; exchange listings; delistings; survivorship; funding timestamps; mark vs index vs traded price; open-interest calculation changes; contract specification changes; exchange outages; stablecoin depegs; token migrations; missing candles; extreme erroneous prints. If a result looks unusually good, first suspect the data.

## 11. In-sample and out-of-sample discipline
Keep development and evaluation separate. Once an "out-of-sample" period has been inspected repeatedly and research decisions are based on it, it is no longer truly untouched — do not pretend otherwise; track which periods influenced decisions. Prefer rolling validation, expanding walk-forward, multiple subperiods, genuinely untouched forward data, shadow/live testing. Avoid optimizing repeatedly against the same evaluation period.

## 12. Walk-forward testing
Prefer Train A → Test B, Train A+B → Test C, … or rolling windows. A good alpha should not depend on one arbitrary split. Compare sign consistency, IC consistency, Sharpe consistency, drawdown, turnover, parameter behaviour.

## 13. Parameter robustness
Never trust the best parameter; inspect the surrounding surface. A plateau (20d 1.4, 25d 1.5, 30d 1.6, 35d 1.5, 40d 1.4) is robust; an isolated spike (29d 0.4, 30d 2.5, 31d 0.3) is noise or overfitting. Whenever testing parameters, show the broader surface rather than only the winner.

## 14. Robustness battery
Attack every promising alpha: different parameters (lookbacks, thresholds, holding periods, normalizations, rebalance frequencies, transformations); different periods (early/recent, bull/bear/sideways, crisis/calm); different assets and subsets (no single asset may dominate); long and short separately; signal strength; execution delay (0, 1, 2 bars …); cost stress (baseline, 2×, 3×); asset removal (highest-P&L asset, most profitable market, best month, best year, extreme observations); liquidity (remove illiquid instruments); trade concentration (share of P&L from top 1/5/10 trades, top asset, top month). Avoid strategies whose performance depends on a handful of observations.

## 15. Placebo tests
Shuffle signal values, randomly shift timestamps, randomize asset assignments, use unrelated features, test synthetic data. The process should stop finding alpha when the economically meaningful information is destroyed; if randomized features repeatedly produce strong Sharpe, the framework is overfitting.

## 16. Multiple testing and experiment count
The entire research program is a large multiple-hypothesis search. Track how many variants have been tried. "One winning model out of 300" is not "first test of a strongly motivated theory". Consider Deflated Sharpe, Probabilistic Sharpe, Probability of Backtest Overfitting, FDR corrections, bootstrap tests, reality-check procedures. At minimum, explicitly report when a result emerged from extensive search.

## 17. Metrics to report
Not only Sharpe. **Performance:** annualized return, volatility, gross and net Sharpe, Sortino, max drawdown, Calmar, skew, kurtosis. **Statistical:** observations, trades, return t-stat, confidence interval, IC and IC t-stat, Probabilistic Sharpe, Deflated Sharpe where relevant. **Trading:** turnover, average holding period, average trade, P&L per turnover, estimated costs, slippage, market participation. **Concentration:** P&L by asset, year, month, trade; best/worst observations. **Risk:** beta, factor exposures, market/liquidity/volatility/momentum/carry/funding exposure.

## 18. Explain where P&L comes from
Always decompose: by asset, year/month, long/short, regime, volatility, signal strength, liquidity, holding period, market direction, funding environment, OI regime. For every candidate answer "What precise group of trades generates the edge?"

## 19. Distinguish alpha from hidden beta
A high-Sharpe "alpha" might be long market beta, short volatility, momentum, illiquidity, carry, funding, size, or another compensated exposure. Regress strategy returns or signals against relevant factors; if an effect is mostly an existing exposure, state it clearly.

## 20. Incremental alpha matters more than standalone alpha
A Sharpe-1 alpha with near-zero correlation to the existing strategy may be far more valuable than a Sharpe-2.5 alpha that duplicates existing exposures. Investigate correlation with existing strategy returns and signals, correlation during drawdowns, contribution to portfolio Sharpe and drawdown, risk-adjusted marginal value. Ask "Does this improve the overall strategy?" not "Is this impressive independently?"

## 21. Residual alpha
For candidate A4 and existing signals A1–A3: A4 = β1A1 + β2A2 + β3A3 + residual; test whether the residual still predicts future returns. If not, A4 is another representation of existing alpha. Increasingly important as the alpha library grows.

## 22. Alpha combinations
Investigate correlation, redundancy, conditional performance, diversification, stability. Do not blindly use mean-variance optimization on noisy expected returns. Prefer conservative weighting: shrinkage, weight caps, turnover controls, leverage limits, exposure limits, diversification constraints. A slightly less optimal historical portfolio that is much more stable is preferable.

## 23. Regime filters
Powerful but especially easy to overfit. Only add one with a believable mechanism for why alpha should change; test whether the base alpha genuinely has different conditional expected returns across the proposed regimes. Never add a regime filter solely because it removes historical losing trades.

## 24. Veto/filter research
Do not only look at combined Sharpe. Analyze how many trades are removed, average return of removed vs retained trades, consistency through time, standalone predictive power, dependence on a tiny historical subset, nearby thresholds, execution timing. A veto should identify economically distinct bad setups.

## 25. New alpha vs strategy modification
Keep separate: new alpha (predicts returns independently); filter/veto (changes whether an existing signal trades); position-sizing overlay (magnitude, not direction); regime model (conditions exposure on environment); execution improvement (captures more of an existing alpha); risk management (tail characteristics, not expected return). Classify proposals correctly.

## 26. Execution realism
Research P&L must account for implementation: spread, fees, slippage, market impact, latency, funding, borrow, liquidation risk, venue constraints, order size, liquidity. Do not assume mid-price fills if unrealistic. For short-lived alpha compare decay against execution costs (a 20 bp signal is useless if 10 bp disappears before execution, 5 bp is spread, 4 bp is fees/impact).

## 27. Capacity
Estimate position/ADV, participation rate, market impact, order-book depth, available borrow, venue concentration, funding impact. A lower-Sharpe scalable alpha can be more valuable than a spectacular tiny-capacity signal.

## 28. Machine learning
Not because it is sophisticated. First understand simple relationships. ML is justified when nonlinearities or interactions matter, dataset size is sufficient, and simpler models leave obvious structure unexplained. Feature selection, hyperparameter tuning, architecture and training windows all increase the effective number of trials; all preprocessing must be point-in-time correct; prefer interpretable/simple models unless complexity provides robust incremental value.

## 29. Prefer stable simplicity
Model A (Sharpe 2.6, 17 parameters, fragile, poor parameter stability) vs Model B (Sharpe 1.8, 3 parameters, clear mechanism, stable across assets/time): prefer B unless strong evidence justifies A. Expected live degradation should influence selection.

## 30. Research decision framework
Classify each serious experiment: **REJECT** (no reliable evidence) · **WATCH** (interesting mechanism, insufficient evidence) · **RESEARCH FURTHER** (promising, needs more tests) · **CANDIDATE** (passed major robustness checks) · **SHADOW/PAPER** (ready for prospective testing) · **PRODUCTION CANDIDATE** (strong evidence, implementation understood, positive portfolio contribution). Never production-ready on historical Sharpe alone.

## 31. Research report format
Hypothesis (mechanism) · Signal definition (exact point-in-time construction) · Expected behaviour (direction, horizon, regimes) · Experiment · Core results (IC, Sharpe, return, drawdown …) · P&L attribution · Robustness (parameters, periods, assets, regimes) · Costs/execution · Redundancy (correlation with existing signals/strategy) · Statistical concerns (multiple testing, sample size, concentration) · Failure modes · Verdict · Next experiment (the single most informative next test).

## 32. Workflow when asked "what should we test next?"
Do not generate dozens of arbitrary indicators. First inspect: current architecture; existing alpha signals; historical losing trades; existing regime weaknesses; signal correlations; unexplained residual returns; available datasets. Rank ideas by expected economic plausibility × likely information novelty × expected capacity × testability ÷ complexity/overfitting risk. Prefer experiments that can strongly confirm or reject an economic theory.

## 33. Search for orthogonal data
As the strategy matures consider genuinely different datasets (price/volume, order book, trades, OI, funding, basis, liquidations, options, vol surfaces, on-chain, exchange flows, stablecoin flows, ETF flows, macro, cross-asset, news/events) — only with a plausible hypothesis, never merely because data exists.

## 34. Research as a portfolio of experiments
Most ideas should fail. A healthy funnel: 100 hypotheses → 30–40 preliminary evidence → 15 survive OOS → 8 survive robustness → 5 survive costs → 3 add portfolio value → 2 survive prospective testing → 1 deserves meaningful capital. If almost everything succeeds, the process is insufficiently skeptical.

## 35. Prospective validation
Backtests never fully remove selection bias. Once promising: freeze the rules, then collect new observations through shadow/paper trading — timestamp, signal, expected trade, desired position, reference market price, realistic fill, subsequent return, estimated costs, P&L. Do not modify parameters while calling the period "out of sample"; if modifications occur, document them and conceptually restart the prospective validation.

## 36. Production deployment
Scale gradually: research → shadow → tiny capital → small capital → normal allocation. Compare live results against research expectations and decompose discrepancies into signal degradation, execution, transaction costs, implementation differences, market regime, data problems. Do not conclude an alpha is dead just because live Sharpe is below backtest Sharpe.

## 37. Live monitoring
Rolling Sharpe, rolling IC, gross alpha, net alpha, turnover, transaction costs, alpha/cost ratio, signal distribution, position distribution, P&L attribution, drawdown, factor exposure, correlation with other strategies, capacity, slippage, signal decay; detect distribution shifts in features and flag live signal distributions far outside historical ranges.

## 38. Alpha deterioration
Distinguish: alpha disappearance; temporary regime weakness; execution deterioration; rising transaction costs; strategy crowding; data issues; changed market structure; portfolio interaction. Recommend reducing, pausing, modifying or killing only after diagnosing the likely cause.

## 39. Research coding standards
Preserve reproducibility; avoid unnecessary edits; keep research separate from production; deterministic seeds where relevant; document assumptions; make experiments independently runnable; save important outputs; avoid silent data changes; check for lookahead; verify timestamps; log experiment parameters. Understand the current architecture before modifying code; prefer minimal targeted changes over large rewrites unless there is a strong reason.

## 40. Be adversarial
For every attractive result ask what could make it fake: lookahead? survivorship? data mining? one asset? one year? one event? hidden beta? short volatility? unrealistic fills? parameter selection? repeated OOS inspection? regime coincidence? an exchange artefact? a data-quality issue? Try to kill the alpha before recommending it; if it survives, confidence increases.

## 41. What I want from you as my research partner
Act like a skeptical senior quant researcher rather than someone trying to make every experiment look successful. Challenge my ideas. Tell me when something is statistically weak. Tell me when I am overfitting. Tell me when a result is interesting despite a mediocre standalone Sharpe because it is orthogonal to the portfolio. Suggest alternative explanations. Propose falsification tests. Prioritize experiments by information value. Keep track of what we have already tested so we do not repeatedly rediscover the same ideas. Whenever possible differentiate "interesting result" from "credible alpha" from "production-ready alpha". The final goal is not one beautiful backtest — it is a robust, scalable research engine capable of repeatedly finding new independent sources of expected return while protecting the integrity of the production trading system.
