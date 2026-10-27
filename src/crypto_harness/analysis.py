"""Point-in-time chart summaries; indicators inform the AI but never place orders."""
from __future__ import annotations

import numpy as np
import ta_patterns as tap


def _ema(values, span):
    alpha = 2 / (span + 1)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1 - alpha) * result
    return result


def analyze_ohlcv(rows, timeframe):
    """Summarise closed bars only; no forward return or current partial bar."""
    if len(rows) < 35:
        return {"status": "insufficient_history", "timeframe": timeframe, "bars": len(rows)}
    o = np.asarray([row["open"] for row in rows], dtype=float)
    h = np.asarray([row["high"] for row in rows], dtype=float)
    l = np.asarray([row["low"] for row in rows], dtype=float)
    c = np.asarray([row["close"] for row in rows], dtype=float)
    v = np.asarray([row["volume"] for row in rows], dtype=float)
    change = np.diff(c)
    gains, losses = np.maximum(change, 0)[-14:], -np.minimum(change, 0)[-14:]
    rs = gains.mean() / max(losses.mean(), 1e-12)
    rsi = 100 - 100 / (1 + rs)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr = tr[-14:].mean()
    # Full scanner: 106 candlestick + 194 chart-pattern detectors. It is fed
    # only closed bars collected above, so no partial future bar leaks in.
    signals = tap.scan_all_patterns(o, h, l, c, v=v)
    active = sorted(name for name, values in signals.items() if int(values[-1]) != 0)[:12]
    ema_fast, ema_slow = _ema(c[-21:], 8), _ema(c[-35:], 21)
    return {
        "status": "ok", "timeframe": timeframe, "bars": len(rows), "close": round(float(c[-1]), 8),
        "trend": "bullish" if ema_fast > ema_slow else "bearish" if ema_fast < ema_slow else "flat",
        "ema_fast": round(ema_fast, 8), "ema_slow": round(ema_slow, 8), "rsi_14": round(float(rsi), 3),
        "atr_pct": round(float(atr / c[-1] * 100), 4), "support": round(float(l[-20:].min()), 8),
        "resistance": round(float(h[-20:].max()), 8), "volume_ratio": round(float(v[-1] / max(v[-20:].mean(), 1e-12)), 3),
        "patterns": active,
    }
