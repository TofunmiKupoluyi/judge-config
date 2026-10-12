# VERBATIM COPY of Obsidian backtest/engine_v2.py (MASTER_VERSION below) — the production replica engine the live trader is kept in
# lockstep with. Judge runs it with dd_ladder / dd_cppi / dd_glide OFF (tools/engine.py HARNESS_P); nothing else is changed here.
# To refresh: copy the file from the Obsidian repo again and re-run tests/test_engine.py. Do not edit in place.
"""Engine v2 — reward-function sizing framework (user spec 2026-07-20).

Differences vs engine.py (v1):
- NO vol targeting, NO inverse-vol weighting, NO drawdown ladder.
- Position size comes from the alpha reward itself:
      pos_$ = sign(edge) * (|edge_bps| - gamma)^+ * 1e-4 * sqrt(MDV21_$) * reward_scale
  (liquidity-scaled optimal size implied by a sqrt-ADV inventory penalty).
- Per-name cap: min( max( min(20% OI, 30% MDV21, 15% mcap), 0.1% AUM ), 5% NAV ).
- Fund-level limits: |Delta| <= 10% AUM, |Beta_$ vs BTC| <= 10% AUM, GMV <= 4x AUM.
- Single hedge instrument: BTC perp. Betas: rolling 60d vs BTC, clipped [-3, 3].
- Universe hysteresis handled upstream (univ + no_entry masks).
- Kept from v1: no-trade band, entry cost hurdle over expected hold,
  participation cap, taker fees, spread+impact costs, per-print funding P&L.
"""

import numpy as np
import pandas as pd

# MASTER config version. Bump in lockstep with live/live_trader.py MASTER_VERSION
# and the git tag on every master change (see VERSION_HISTORY.md protocol).
MASTER_VERSION = "v2.3.1"   # v2.3.1 (2026-10-04): no engine change — the LIVE FundingCarry input was made research-exact (live/funding_feature.py); numbers identical to v2.3.
# v2.3 (2026-09-11): a4 + a12 + a90x_ratio as a SEVEN-PHASE weekly ensemble (E98/E98b): target = mean of the 7 phase books
                          # (research_e98b.py build -> ens7_both panels; run spec V2_WTS="ens7_both_edge:1.0" V2_EXTRA="ens7_both" size_components=[]); RISK2 unchanged

H = 24

RISK2 = {
    "aum_usd": 10e6,
    "reward_scale": 40000.0,     # calibrated so calib-window avg GMV ~ 3x AUM
    "gamma_bps": 5.0,
    "expected_hold_d": 5.0,
    "band_frac": 0.50,
    "band_abs": 0.001,
    "adjust_every_h": 4,
    "participation_cap": 0.05,
    "taker_fee_bps": 4.0,
    "spread_bps_liquid": 1.0,
    "spread_bps_mid": 3.0,
    "spread_bps_illiquid": 8.0,
    "impact_k_bps": 15.0,
    "pos_cap_nav": 0.05,         # NAV concentration leg [D]
    # liquidity-leg fractions (tenth-scale tested 2026-07-20 and REJECTED:
    # halved eval returns, no DD improvement; original QRT-scale legs kept)
    "cap_frac_oi": 0.20,
    "cap_frac_mdv": 0.30,
    "cap_frac_mcap": 0.15,
    # max-loss limit (2026-07-20): stress move by MDV21 tier; loss <= 5% AUM.
    # tiers (2026-07-21): Large >= $100M, Medium $10-100M, Small < $10M
    # v2.1 (2026-09-03, ADOPTED, disclosed post-hoc hardening after stress-
    # episode review E61/E61b): short squeeze stress 100/150/300 -> 200/300/500%
    # (short caps 2.5/1.67/1.0% NAV); long collapse stress 30/50/80 -> 50/65/80%
    # (implied 10/7.7/6.25% NAV, non-binding behind the 5% NAV concentration
    # cap -> long book unchanged). Prior-v2.0 values in git history.
    "maxloss_frac": 0.05,
    "stress_long": {"large": 0.50, "medium": 0.65, "small": 0.80},
    "stress_short": {"large": 2.00, "medium": 3.00, "small": 5.00},
    "delta_cap": 0.10,           # |net delta| <= 10% AUM
    "beta_cap": 0.10,            # |net beta-$| <= 10% AUM
    "gmv_cap": 4.0,              # GMV <= 4x AUM
    "hedge_band": 0.02,
    # fund-level vol governor (2026-07-21): defensive only — scales the book
    # DOWN when trailing 30d realized book vol exceeds the ceiling; never up.
    "vol_ceiling_ann": 0.0,      # 0 disables (governor tested, not adopted)
    # Nickel-mandated drawdown ladder — ADOPTED as master v2.2 (2026-09-03):
    # gross cut to 50% / 25% at 5.5% / 7.5% DD from running HWM (triggers set
    # 0.5pp inside DAL's "before 6%/8%" thresholds so confirmed cuts complete
    # in time), restored at <4% / <6%. Evaluated hourly on compounded-NAV DD
    # (~notional basis assuming quarterly P&L sweeps). Hysteresis gaps are the
    # anti-jitter mechanism (~22 state changes/yr in backtest).
    "dd_ladder": True,
    "dd_cut1": 0.055, "dd_cut2": 0.075,  # gross -> 50% / 25% at these DDs
    "dd_rest1": 0.04, "dd_rest2": 0.06,  # restore 100% / 50% below these
    # dwell smoothing: DD must persist N consecutive hourly marks before a
    # cut / restore fires. Tested 3h/6h, 3h/24h, 6h/48h (E66) — ALL worse on
    # return, Sharpe and DD than instant; kept at 1 (instant), params remain.
    "dd_dwell_cut": 1, "dd_dwell_restore": 1,
    # optional third rung ("soft stop", 2026-09-03): gross -> dd_mult3 at
    # dd_cut3, restored to 25% below dd_rest3. 0.0 disables (v2.2 default).
    # Never fully flat: a flat book freezes its DD and can never recover.
    "dd_cut3": 0.0, "dd_rest3": 0.06, "dd_mult3": 0.10,
    # optional continuous glidepath (2026-09-03, E68; overrides the step
    # ladder when on): gross mult = floor + (1-floor) * ((end-dd)/(end-start))
    # ^exp, clipped [floor, 1] — full book at dd<=start, floor at dd>=end,
    # instantly reversible both ways (no hysteresis dead zones). De-risking
    # trades bypass the no-trade band and the 4h adjust gate while active.
    "dd_glide": False, "dd_glide_start": 0.03, "dd_glide_end": 0.09,
    "dd_glide_floor": 0.10, "dd_glide_exp": 1.0,
    # optional CPPI-style distance/risk governor (2026-09-03, E68; overrides
    # ladder & glide): mult = clip(cushion / (k * sigma_daily_unscaled), floor,
    # 1) with cushion = cppi_stop - dd. Vol-anticipatory AND depth-aware;
    # continuous, instantly reversible. sigma = EWMA of unscaled book daily
    # returns (realized / prior mult), span cppi_vol_span_d.
    "dd_cppi": False, "cppi_stop": 0.095, "cppi_k": 6.0,
    "cppi_floor": 0.05, "cppi_vol_span_d": 10,
    # per-name cumulative-loss cooldown (2026-09-03): close + freeze a name once
    # its rolling net contribution (price+funding) burns cooldown_loss_frac of
    # NAV within cooldown_window_d; release once losses age out to
    # release_ratio * trigger. Motivated by re-entry bleed in the 2026-02..04
    # pump/dump episode (SIREN/RIVER/BULLA). 0 disables.
    "cooldown_loss_frac": 0.0,
    "cooldown_window_d": 30,
    "cooldown_release_ratio": 0.5,
}


def max_loss_caps(mdv21, p):
    """Long/short cap panels (weights) from the stress-scenario max-loss limit.
    maxloss_frac_long (2026-09-03, default = maxloss_frac): separate long-side
    loss budget; with stress_long <= 100% the long leg only binds below the 5%
    NAV concentration cap when this budget is set below maxloss_frac."""
    ml = p["maxloss_frac"]
    ml_long = p.get("maxloss_frac_long", ml)
    sl_ = np.where(mdv21 >= 100e6, p["stress_long"]["large"],
          np.where(mdv21 >= 10e6, p["stress_long"]["medium"],
                   p["stress_long"]["small"]))
    ss_ = np.where(mdv21 >= 100e6, p["stress_short"]["large"],
          np.where(mdv21 >= 10e6, p["stress_short"]["medium"],
                   p["stress_short"]["small"]))
    cap_l = pd.DataFrame(ml_long / sl_, index=mdv21.index, columns=mdv21.columns)
    cap_s = pd.DataFrame(ml / ss_, index=mdv21.index, columns=mdv21.columns)
    return cap_l, cap_s


def size_targets(edge_bps, univ, mdv21, cap_w, p=RISK2, scale=None, scale_post=None, extra=None):
    """Reward sizing + every cap -> per-name target weight panel (the vectorised preamble of
    run(); also used by research scripts that need the master's targets). Returns the target
    panel and the intermediates run() drops after conversion to numpy."""
    aum = p["aum_usd"]
    g = p["gamma_bps"]
    net = (edge_bps.abs() - g).clip(lower=0.0) * 1e-4
    if p.get("size_mode", "sqrt_mdv") == "flat":          # research E79: no sqrt(MDV) in sizing
        raw_usd = np.sign(edge_bps) * (net > 0) * float(p.get("flat_usd", 100000.0))
    else:
        raw_usd = np.sign(edge_bps) * net * np.sqrt(mdv21.clip(lower=0.0)) \
            * p["reward_scale"]
    tgt = (raw_usd / aum).where(univ, 0.0).fillna(0.0)
    if extra is not None:                                   # research E79b: externally sized sleeve, caps still apply
        tgt = (tgt + extra.reindex_like(tgt).fillna(0.0)).where(univ, 0.0)
    if scale is not None:
        sc = scale.reindex_like(tgt).fillna(1.0)
        ss = p.get("scale_sign")                            # research E155: apply the scale panel to one side only (None = both, bit-exact)
        if ss == "long":
            tgt = tgt.where(tgt <= 0, tgt * sc)
        elif ss == "short":
            tgt = tgt.where(tgt >= 0, tgt * sc)
        else:
            tgt = tgt * sc
        del sc
    tgt = tgt.clip(-cap_w, cap_w, axis=None)
    # max-loss limit: asymmetric long/short caps, min'd on top of existing limits
    cap_l, cap_s = max_loss_caps(mdv21, p)
    tgt = tgt.clip(-cap_s, cap_l, axis=None)
    # GMV pre-scale at target level
    gmv = tgt.abs().sum(axis=1)
    over = (p["gmv_cap"] / gmv).clip(upper=1.0).fillna(1.0)
    tgt = tgt.mul(over, axis=0)
    if scale_post is not None:
        sp = scale_post.reindex_like(tgt).fillna(1.0).to_numpy(dtype="float32")
        tgt = pd.DataFrame(tgt.to_numpy() * sp, index=tgt.index, columns=tgt.columns)
        del sp
    return tgt, net, raw_usd, gmv, over, cap_l, cap_s


def run(close, qv, fund_hourly, edge_bps, edge_components, univ, no_entry,
        beta_btc, mdv21, cap_w, btc_ret, p=RISK2, nav0=1.0, scale=None, eth=None, scale_post=None, extra=None,
        mult_override=None):
    """cap_w: per-name cap panel in NAV-weight terms (precomputed).
    mdv21: $ panel. btc_ret: BTC hourly returns (hedge instrument).
    scale: optional per-name/hour multiplier on the reward-sized targets (research
    overlays, e.g. E75 alt-dominance regime; applied before the caps). None = master.
    eth: optional second hedge instrument (E77 research, default None = master): dict with
    'ret' (ETH hourly return), 'fund' (ETH funding at print hours), 'beta_btc' (ETH's rolling
    beta to BTC) and 'beta_res' (panel: each name's beta to the ETH residual factor). Each hour
    the ETH hedge neutralises the alt book's ETH-residual exposure (band, cap), then the BTC
    hedge is solved as before on the book INCLUDING the ETH hedge's BTC beta and delta.
    scale_post: optional per-name/hour multiplier applied AFTER every cap (research, E79/E83
    leg balancing). p['size_mode']='flat' replaces sqrt(MDV) sizing by a fixed p['flat_usd']
    per name (research, E79). p['derisk_priority'] in {'a4','a12'} allocates the governor's
    gross budget to that alpha first (research, E84). All default off = master."""
    idx, cols = close.index, close.columns
    rets = close.pct_change(fill_method=None).fillna(0.0)
    T, N = len(idx), len(cols)
    aum = p["aum_usd"]

    # ---- reward sizing (vectorized) ----
    size_edge = edge_bps
    if p.get("size_components") is not None:                 # research E79b/E88: size only these alphas' edges
        comps = [edge_components[k] for k in p["size_components"]]
        size_edge = sum(comps) if comps else edge_bps * 0.0  # [] = nothing sized here (targets come from `extra`)
    tgt, net, raw_usd, gmv, over, cap_l, cap_s = size_targets(size_edge, univ, mdv21, cap_w, p, scale, scale_post, extra)

    tgt_np = tgt.to_numpy()
    edge_np = edge_bps.to_numpy()
    rets_np = rets.to_numpy()
    fund_np = fund_hourly.fillna(0.0).to_numpy()
    qv_np = np.nan_to_num(qv.to_numpy(), nan=0.0)
    mdv_np = np.nan_to_num(mdv21.to_numpy(), nan=0.0)
    beta_np = np.nan_to_num(beta_btc.to_numpy(), nan=1.0)
    btc_np = btc_ret.fillna(0.0).to_numpy()
    noent_np = no_entry.fillna(False).to_numpy()
    aeh = int(p.get("alpha_every_h", 0) or 0)                 # research E114: alpha-driven trades only every aeh hours (0 = master)
    rec = bool(p.get("record_trades", False))                 # research E120: per-trade ledger (open/add/reduce/close, de-risk, universe exit)
    dba = float(p.get("derisk_band_abs", 0.0) or 0.0)         # research E126: minimum |dw| for a governor de-risk trade (0 = master: any size)
    rec_t = bool(p.get("record_targets", False))              # validation round: hourly post-governor target panel (deferred-order audit)
    tw_hist = np.zeros((T, N), dtype=np.float32) if rec_t else None
    univ_np = univ.fillna(False).to_numpy(dtype=bool) if (aeh or rec) else None
    trade_rows = []
    if eth is not None:
        eth_ret_np = eth["ret"].reindex(idx).fillna(0.0).to_numpy()
        eth_fund_np = eth["fund"].reindex(idx).fillna(0.0).to_numpy()
        eth_bb_np = eth["beta_btc"].reindex(idx).fillna(1.0).to_numpy()
        eth_bres_np = np.nan_to_num(eth["beta_res"].reindex(index=idx, columns=cols).to_numpy(), nan=0.0)
        eth_cap = float(p.get("eth_hedge_cap", 0.5))
    eth_w = 0.0
    eth_hist = np.zeros(T)
    hs = np.where(mdv_np >= 100e6, p["spread_bps_liquid"],
         np.where(mdv_np >= 20e6, p["spread_bps_mid"], p["spread_bps_illiquid"]))
    fee = p["taker_fee_bps"]

    # attribution shares
    comp_tgt = {}
    tot_signed = sum(edge_components.values()).replace(0, np.nan)
    for name, e in edge_components.items():
        comp_tgt[name] = (e / tot_signed).clip(-3, 3).fillna(0.0)
    comp_np = {k: v.to_numpy(dtype=np.float32) for k, v in comp_tgt.items()}
    comp_pnl = {k: np.zeros(T) for k in comp_tgt}
    attr = bool(p.get("attr_full", False))                    # research E122: also attribute funding, costs and turnover by edge share
    if attr:
        comp_fund = {k: np.zeros(T) for k in comp_tgt}; comp_cost = {k: np.zeros(T) for k in comp_tgt}; comp_turn = {k: np.zeros(T) for k in comp_tgt}
        abs_sum = sum(np.abs(v) for v in comp_np.values()); abs_sum[abs_sum == 0] = 1.0
        comp_abs = {k: np.abs(v) / abs_sum for k, v in comp_np.items()}; del abs_sum
    # memory (2026-09-10): the vectorised preamble is finished — drop the pandas temporaries
    # before the hourly loop (the eval-window sim peaked at 3.7 GB on a 4 GB box). No effect
    # on results: everything below reads the numpy arrays only.
    del net, raw_usd, tgt, gmv, over, cap_l, cap_s, rets, comp_tgt, tot_signed
    import gc; gc.collect()
    try:
        import ctypes; ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

    w = np.zeros(N); hedge_w = 0.0
    nav = np.empty(T); nav[0] = nav0
    w_hist = np.zeros((T, N), dtype=np.float32)
    pnl_alpha = np.zeros(T); pnl_fund = np.zeros(T); pnl_hedge = np.zeros(T)
    cost_arr = np.zeros(T); turnover = np.zeros(T); req_turnover = np.zeros(T)
    beta_hist = np.zeros(T); delta_hist = np.zeros(T); hedge_hist = np.zeros(T)

    from collections import deque
    dret = deque(maxlen=30)      # trailing daily returns for the vol governor
    last_day_nav = nav0
    governor = 1.0
    vc = p.get("vol_ceiling_ann", 0.0)
    ladder = bool(p.get("dd_ladder", False))
    glide = bool(p.get("dd_glide", False))
    cppi = bool(p.get("dd_cppi", False))
    sig_u = 0.015            # unscaled daily vol estimate (seed 1.5%)
    cppi_lam = 2.0 / (float(p.get("cppi_vol_span_d", 10)) + 1.0)
    dd_mult = 1.0
    hwm = nav0
    ladder_hist = np.ones(T)
    la1 = la2 = lb1 = lb2 = 0   # dwell counters (above cut1/2, below rest1/2)
    cd_thr = p.get("cooldown_loss_frac", 0.0)
    blocked_n = np.zeros(T)
    if cd_thr:
        cd_win = int(p.get("cooldown_window_d", 30))
        cd_rel = cd_thr * p.get("cooldown_release_ratio", 0.5)
        cd_buf = np.zeros((cd_win, N)); cd_i = 0
        cd_static = np.zeros(N); cd_day = np.zeros(N)
        cd_blocked = np.zeros(N, dtype=bool)
    mo_np = None
    if mult_override is not None:          # Series indexed like the panels -> float array (NaN = keep own governor)
        mo_np = mult_override.reindex(idx).ffill().fillna(1.0).to_numpy(dtype="float64")
    for t in range(1, T):
        nav_t = nav[t - 1]
        nav_usd = nav_t * aum
        if t % 24 == 0:          # daily mark: update trailing vol + governor
            dr_day = nav_t / last_day_nav - 1.0
            dret.append(dr_day)
            if cppi:             # unscaled-book vol proxy for the governor
                dr_u = dr_day / max(dd_mult, 0.05)
                sig_u = float(np.sqrt((1.0 - cppi_lam) * sig_u ** 2
                                      + cppi_lam * dr_u ** 2))
            last_day_nav = nav_t
            if vc and len(dret) >= 15:
                rv = float(np.std(dret)) * np.sqrt(365)
                governor = min(1.0, vc / rv) if rv > 0 else 1.0
        pa = float(np.dot(w, rets_np[t]))
        pf = float(np.dot(w, -fund_np[t]))
        ph = float(hedge_w * btc_np[t])
        if eth is not None:
            ph += float(eth_w * eth_ret_np[t])
            pf += float(-eth_w * eth_fund_np[t])      # the ETH hedge pays/receives funding like any position
        for k in comp_np:
            comp_pnl[k][t] = float(np.dot(w * comp_np[k][t - 1], rets_np[t]))
            if attr:
                comp_fund[k][t] = float(np.dot(w * comp_np[k][t - 1], -fund_np[t]))

        if cd_thr:  # per-name rolling net contribution -> block/release
            cd_day += w * rets_np[t] - w * fund_np[t]
            if t % 24 == 0:
                cd_static += cd_day - cd_buf[cd_i]
                cd_buf[cd_i] = cd_day
                cd_i = (cd_i + 1) % cd_win
                cd_day = np.zeros(N)
            roll = cd_static + cd_day
            cd_blocked = (cd_blocked | (roll <= -cd_thr)) & (roll < -cd_rel)
            blocked_n[t] = float(cd_blocked.sum())

        if cppi:  # CPPI-style: mult = cushion / (k * sigma), vol-anticipatory
            hwm = max(hwm, nav_t)
            dd = 1.0 - nav_t / hwm
            cushion = p.get("cppi_stop", 0.095) - dd
            k_ = p.get("cppi_k", 6.0)
            fl_ = p.get("cppi_floor", 0.05)
            if cushion <= 0.0:
                dd_mult = fl_
            else:
                dd_mult = min(1.0, max(fl_, cushion / max(k_ * sig_u, 1e-6)))
            if p.get("cppi_nickel_clamp", True):  # DAL schedule as hard envelope
                if dd >= 0.075:
                    dd_mult = min(dd_mult, 0.25)
                elif dd >= 0.055:
                    dd_mult = min(dd_mult, 0.50)
            ladder_hist[t] = dd_mult
        elif glide:  # continuous distance-to-stop glidepath (overrides ladder)
            hwm = max(hwm, nav_t)
            dd = 1.0 - nav_t / hwm
            gs = p.get("dd_glide_start", 0.03)
            ge = p.get("dd_glide_end", 0.09)
            gf = p.get("dd_glide_floor", 0.10)
            gx = p.get("dd_glide_exp", 1.0)
            frac = min(max((ge - dd) / max(ge - gs, 1e-9), 0.0), 1.0)
            dd_mult = gf + (1.0 - gf) * (frac ** gx)
            ladder_hist[t] = dd_mult
        elif ladder:  # Nickel-style schedule, thresholds parametric, dwell-smoothed
            hwm = max(hwm, nav_t)
            dd = 1.0 - nav_t / hwm
            c1, c2 = p.get("dd_cut1", 0.06), p.get("dd_cut2", 0.08)
            r1, r2 = p.get("dd_rest1", 0.04), p.get("dd_rest2", 0.06)
            dw_c = int(p.get("dd_dwell_cut", 1))
            dw_r = int(p.get("dd_dwell_restore", 1))
            c3 = p.get("dd_cut3", 0.0)
            r3, m3 = p.get("dd_rest3", 0.06), p.get("dd_mult3", 0.10)
            la1 = la1 + 1 if dd >= c1 else 0
            la2 = la2 + 1 if dd >= c2 else 0
            lb1 = lb1 + 1 if dd < r1 else 0
            lb2 = lb2 + 1 if dd < r2 else 0
            if c3 and dd >= c3 and dd_mult > m3:
                dd_mult = m3                     # soft stop (instant)
            elif dd_mult == 1.0:
                if la2 >= dw_c:
                    dd_mult = 0.25
                elif la1 >= dw_c:
                    dd_mult = 0.50
            elif dd_mult == 0.50:
                if la2 >= dw_c:
                    dd_mult = 0.25
                elif lb1 >= dw_r:
                    dd_mult = 1.0
            elif dd_mult == 0.25:
                if lb2 >= dw_r:
                    dd_mult = 0.50
            else:  # at mult3
                if dd < r3:
                    dd_mult = 0.25
            ladder_hist[t] = dd_mult

        if mult_override is not None:      # research (E97 governor 2x2): impose another run's multiplier path
            dd_mult = float(mo_np[t]); ladder_hist[t] = dd_mult
        mult_t = governor * dd_mult
        prio = p.get("derisk_priority")
        if prio and mult_t < 0.999 and prio in comp_np and len(comp_np) == 2:
            # research E84: give the governor's gross budget to one alpha first
            other = [k for k in comp_np if k != prio][0]
            part_p = tgt_np[t] * comp_np[prio][t]; part_o = tgt_np[t] * comp_np[other][t]
            g_p = float(np.abs(part_p).sum()); g_o = float(np.abs(part_o).sum())
            g_star = mult_t * float(np.abs(tgt_np[t]).sum())
            m_p = min(1.0, g_star / g_p) if g_p > 0 else 1.0
            m_o = max(0.0, (g_star - m_p * g_p) / g_o) if g_o > 0 else 1.0
            m_o = min(m_o, 1.0)
            tw = part_p * m_p + part_o * m_o
        else:
            tw = tgt_np[t] * mult_t
        if cd_thr:
            tw = np.where(cd_blocked, 0.0, tw)
        # project TARGETS into the Delta/Beta-feasible set (bug fix 2026-07-22:
        # scaling only held positions caused a rebuy/scale-down oscillation loop
        # whenever long/short leg betas diverged)
        Bt = float(np.dot(tw, beta_np[t])); Dt = float(np.sum(tw))
        if abs(Bt - Dt) > p["beta_cap"] + p["delta_cap"]:
            tw = tw * ((p["beta_cap"] + p["delta_cap"]) / abs(Bt - Dt))
        delta = tw - w
        if rec_t:
            tw_hist[t] = tw
        # entry hurdle: edge accrued over expected hold must beat round-trip cost
        opening = (w == 0) & (tw != 0)
        net_edge = np.abs(edge_np[t]) * p["expected_hold_d"] - 2.0 * (hs[t] + fee)
        blocked = opening & ((net_edge < p["gamma_bps"]) | noent_np[t])
        small = (np.abs(delta) < p["band_frac"] * np.maximum(np.abs(tw), 1e-9)) \
            | (np.abs(delta) < p["band_abs"])
        glide_derisk = None
        if (glide or cppi) and dd_mult < 0.999:
            glide_derisk = (np.abs(tw) < np.abs(w)) & (tw * w >= 0.0)
            if dba > 0.0:                                     # research E126: de-risk trades keep only the absolute band (no 50% band)
                glide_derisk = glide_derisk & (np.abs(delta) >= dba)
            small = small & ~glide_derisk
        do_trade = ~(small | blocked)
        closing = (tw == 0) & (w != 0)
        if t % p["adjust_every_h"]:
            allowed = opening | closing
            if glide_derisk is not None:
                allowed = allowed | glide_derisk
            do_trade = do_trade & allowed
        if aeh and (t % aeh):
            # research E114 (review round 8, B): between the aeh-hour boundaries only governor de-risking trades and
            # exits of names that left the universe execute; every alpha-driven open / close / resize waits for the boundary,
            # where the book trades straight to the latest target (missed intermediate targets are not replayed).
            hourly = closing & ~univ_np[t]
            if glide_derisk is not None:
                hourly = hourly | glide_derisk
            do_trade = do_trade & hourly
            closing = hourly & closing
        do_trade = do_trade | closing
        delta = np.where(do_trade, delta, 0.0)
        req_turnover[t] = float(np.sum(np.abs(delta)))
        max_w = p["participation_cap"] * qv_np[t] / max(nav_usd, 1e-9)
        req_delta = delta
        delta = np.clip(delta, -max_w, max_w)
        if rec:                                                # research E120: record every executed trade with its engine classification
            ii = np.nonzero(delta)[0]
            if len(ii):
                gd = glide_derisk[ii] if glide_derisk is not None else np.zeros(len(ii), dtype=bool)
                trade_rows.append(np.column_stack([
                    np.full(len(ii), t), ii, delta[ii], req_delta[ii], w[ii], w[ii] + delta[ii], opening[ii], closing[ii], gd,
                    closing[ii] & ~univ_np[t][ii], np.full(len(ii), mult_t), edge_np[t][ii], hs[t][ii],
                    np.abs(delta[ii]) * nav_usd / np.maximum(qv_np[t][ii], 1.0), mdv_np[t][ii], np.full(len(ii), nav_usd)]))
        w = w + delta

        trade = np.abs(delta)
        slip = hs[t] + p["impact_k_bps"] * np.sqrt(
            np.minimum(trade * nav_usd / np.maximum(mdv_np[t], 1.0), 1.0))
        cost = float(np.sum(trade * (fee + slip) * 1e-4))
        turnover[t] = float(np.sum(trade))
        if attr:
            for k in comp_np:
                comp_cost[k][t] = float(np.sum(trade * comp_abs[k][t] * (fee + slip) * 1e-4)); comp_turn[k][t] = float(np.sum(trade * comp_abs[k][t]))

        # ---- fund-level hedge: BTC to satisfy |beta|<=cap AND |delta|<=cap ----
        B = float(np.dot(w, beta_np[t]))   # beta-$ of alt book (per NAV)
        D = float(np.sum(w))               # delta of alt book
        if eth is not None:                # E77: ETH hedge on the book's ETH-residual exposure first
            new_eth = float(np.clip(-np.dot(w, eth_bres_np[t]), -eth_cap, eth_cap))
            if abs(new_eth - eth_w) > p["hedge_band"]:
                cost += abs(new_eth - eth_w) * (fee + p["spread_bps_liquid"]) * 1e-4
                eth_w = new_eth
            B += eth_w * eth_bb_np[t]      # the ETH position carries BTC beta and delta of its own
            D += eth_w
        bc_, dc_ = p["beta_cap"], p["delta_cap"]
        lo = max(-B - bc_, -D - dc_)
        hi = min(-B + bc_, -D + dc_)
        if lo > hi:  # infeasible with one instrument -> scale book down
            s = max(0.0, (bc_ + dc_) / max(abs(B - D), 1e-9))
            s = min(s, 1.0)
            scale_trade = np.abs(w) * (1 - s)
            cost += float(np.sum(scale_trade * (fee + hs[t]) * 1e-4))
            turnover[t] += float(np.sum(scale_trade))
            w = w * s
            B, D = B * s, D * s
            lo = max(-B - bc_, -D - dc_); hi = min(-B + bc_, -D + dc_)
        new_hedge = float(np.clip(-B, lo, hi))
        if abs(new_hedge - hedge_w) > p["hedge_band"]:
            cost += abs(new_hedge - hedge_w) * (fee + p["spread_bps_liquid"]) * 1e-4
            hedge_w = new_hedge

        pnl_alpha[t], pnl_fund[t], pnl_hedge[t], cost_arr[t] = pa, pf, ph, cost
        nav[t] = nav_t * (1.0 + pa + pf + ph - cost)
        w_hist[t] = w
        beta_hist[t] = B + hedge_w
        delta_hist[t] = D + hedge_w
        hedge_hist[t] = hedge_w
        eth_hist[t] = eth_w

    nav_s = pd.Series(nav, index=idx)
    daily = nav_s.resample("1D").last().pct_change(fill_method=None).dropna()
    return {
        "nav": nav_s,
        "daily_returns": daily,
        "weights": pd.DataFrame(w_hist, index=idx, columns=cols),
        "pnl": pd.DataFrame({"alpha": pnl_alpha, "funding": pnl_fund,
                             "hedge": pnl_hedge, "costs": cost_arr,
                             "turnover": turnover, "req_turnover": req_turnover,
                             "beta": beta_hist, "delta": delta_hist,
                             "hedge_w": hedge_hist, "eth_hedge_w": eth_hist,
                             "blocked_n": blocked_n,
                             "ladder_mult": ladder_hist}, index=idx),
        "component_pnl": pd.DataFrame(comp_pnl, index=idx),
        "realized_maker_ratio": 0.0,
        **({"component_fund": pd.DataFrame(comp_fund, index=idx), "component_cost": pd.DataFrame(comp_cost, index=idx),
            "component_turn": pd.DataFrame(comp_turn, index=idx)} if attr else {}),
        **({"targets": pd.DataFrame(tw_hist, index=idx, columns=cols)} if rec_t else {}),
        **({"trades": pd.DataFrame(np.vstack(trade_rows) if trade_rows else np.zeros((0, 16)),
                                   columns=["t", "s", "delta", "req_delta", "w_before", "w_after", "opening", "closing", "derisk",
                                            "univ_exit", "mult", "edge", "half_spread", "participation", "mdv", "nav_usd"])} if rec else {}),
    }
