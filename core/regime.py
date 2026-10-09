"""
GOLD REAPER APEX :: Regime Detector
===================================
Classifies every bar into one of five regimes:

    TREND_UP · TREND_DOWN · RANGE · VOLATILE_CHOP · CRISIS

Primary engine: Gaussian HMM (hmmlearn) over [returns, ATR%, ADX].
The HMM discovers hidden states; a deterministic interpreter maps each
state's signature (drift, vol, trend strength) to a trading regime.

Fallback engine (no hmmlearn / cold data): rule-based classifier on the
same inputs - deterministic, documented, safe. Both paths return the
same RegimeState so callers never branch on which engine ran.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.indicators import adx, atr, ema

REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE_CHOP", "CRISIS")


@dataclass
class RegimeState:
    regime: str = "RANGE"
    probability: float = 0.0
    engine: str = "rules"
    vol_percentile: float = 0.5
    adx: float = 0.0
    state_probs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"regime": self.regime, "probability": round(self.probability, 3),
                "engine": self.engine, "vol_pctile": round(self.vol_percentile, 3),
                "adx": round(self.adx, 1)}


class RegimeDetector:
    name = "APEX-REGIME"

    def __init__(self, n_states: int = 5, warmup: int = 500) -> None:
        self.n_states = n_states
        self.warmup = warmup
        self._model = None
        self._state_map: dict[int, str] | None = None
        self._cols_mean: np.ndarray | None = None
        self._cols_std: np.ndarray | None = None

    # ------------------------------------------------------------- features
    @staticmethod
    def _inputs(df: pd.DataFrame) -> pd.DataFrame:
        ret = df["close"].pct_change()
        a = atr(df, 14) / df["close"]
        dx = adx(df, 14)
        ema_dist = (df["close"] - ema(df["close"], 50)) / df["close"]
        return pd.DataFrame({"ret": ret, "atr": a, "adx": dx, "edist": ema_dist})

    # ------------------------------------------------------------- training
    def fit(self, df: pd.DataFrame) -> bool:
        X = self._inputs(df).dropna()
        if len(X) < self.warmup:
            return False
        try:
            from hmmlearn.hmm import GaussianHMM
            Xs = (X - X.mean()) / X.std().replace(0, 1.0)
            self._cols_mean = X.mean().values
            self._cols_std = X.std().replace(0, 1.0).values
            model = GaussianHMM(n_components=self.n_states, covariance_type="full",
                                n_iter=120, random_state=666)
            model.fit(Xs.values[: -0])
            self._model = model
            self._state_map = self._interpret_states(Xs, model)
            return True
        except Exception:  # noqa: BLE001
            self._model = None
            return False

    def _interpret_states(self, Xs: pd.DataFrame, model) -> dict[int, str]:
        """Map each HMM state to a regime by its signature."""
        means = pd.DataFrame(model.means_, columns=Xs.columns)
        mapping: dict[int, str] = {}
        # crisis: highest vol + highest |negative| drift
        vol_rank = means["atr"].rank(ascending=False)
        drift = means["ret"] + 0.3 * means["edist"]
        for s in means.index:
            if vol_rank[s] == 1 and means.loc[s, "ret"] < 0:
                mapping[s] = "CRISIS"
            elif means.loc[s, "adx"] > 0.35 and drift[s] > 0.15:
                mapping[s] = "TREND_UP"
            elif means.loc[s, "adx"] > 0.35 and drift[s] < -0.15:
                mapping[s] = "TREND_DOWN"
            elif vol_rank[s] <= 2:
                mapping[s] = "VOLATILE_CHOP"
            else:
                mapping[s] = "RANGE"
        # guarantee coverage of all labels
        seen = set(mapping.values())
        for r in REGIMES:
            if r not in seen:
                fallback_s = int(vol_rank.idxmax()) if r == "CRISIS" else int(means.index[0])
                mapping.setdefault(fallback_s, r)
        return mapping

    # ------------------------------------------------------------- inference
    def classify(self, df: pd.DataFrame) -> RegimeState:
        X = self._inputs(df).dropna()
        if len(X) < 60:
            return RegimeState()
        a = float(X["atr"].iloc[-1])
        vol_pctile = float((X["atr"].tail(500) < a).mean())
        dx = float(X["adx"].iloc[-1])

        if self._model is not None and self._state_map is not None:
            try:
                xs = (X - pd.Series(self._cols_mean, index=X.columns)) / \
                    pd.Series(self._cols_std, index=X.columns)
                probs = self._model.predict_proba(xs.values[-30:])
                avg = probs.mean(axis=0)          # smoothed over last 30 bars
                s = int(avg.argmax())
                label = self._state_map.get(s, "RANGE")
                return RegimeState(regime=label, probability=float(avg[s]),
                                   engine="hmm", vol_percentile=vol_pctile,
                                   adx=dx,
                                   state_probs={self._state_map.get(i, f"s{i}"):
                                                round(float(p), 3)
                                                for i, p in enumerate(avg)})
            except Exception:  # noqa: BLE001
                pass

        # rule-based fallback (also the cold-start path)
        edist = float(X["edist"].iloc[-1])
        r20 = float(X["ret"].tail(20).std())
        r120 = float(X["ret"].tail(120).std()) or 1e-9
        vol_ratio = r20 / r120
        if vol_pctile > 0.97 and vol_ratio > 2.2:
            regime = "CRISIS"
        elif dx > 25 and edist > 0.004:
            regime = "TREND_UP"
        elif dx > 25 and edist < -0.004:
            regime = "TREND_DOWN"
        elif vol_ratio > 1.6:
            regime = "VOLATILE_CHOP"
        else:
            regime = "RANGE"
        return RegimeState(regime=regime, probability=0.6, engine="rules",
                           vol_percentile=vol_pctile, adx=dx, state_probs={regime: 0.6})

    # ------------------------------------------------------------- batch (backtest)
    def classify_frame(self, df: pd.DataFrame) -> pd.Series:
        """Vectorized-ish rolling classification for backtests (rule engine)."""
        X = self._inputs(df)
        ret = X["ret"]
        a = X["atr"]
        dx = X["adx"]
        ed = X["edist"]
        vol_pct = a.rolling(500, min_periods=100).rank(pct=True)
        r20 = ret.rolling(20).std()
        r120 = ret.rolling(120).std()
        vr = r20 / r120.replace(0, np.nan)
        out = pd.Series("RANGE", index=df.index)
        out[(vr > 2.2) & (vol_pct > 0.97)] = "CRISIS"
        out[((vr > 1.6) & (vol_pct <= 0.97))] = "VOLATILE_CHOP"
        out[(dx > 25) & (ed > 0.004) & (vr <= 1.6)] = "TREND_UP"
        out[(dx > 25) & (ed < -0.004) & (vr <= 1.6)] = "TREND_DOWN"
        return out.fillna("RANGE")
