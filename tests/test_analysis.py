from crypto_harness.analysis import analyze_ohlcv


def candles(start=100, count=80, step=1):
    rows = []
    for index in range(count):
        close = start + index * step
        rows.append({"open": close - 0.5, "high": close + 1, "low": close - 1, "close": close, "volume": 100 + index})
    return rows


def test_multi_indicator_snapshot_is_bullish_for_rising_chart():
    result = analyze_ohlcv(candles(), timeframe="15m")
    assert result["timeframe"] == "15m"
    assert result["trend"] == "bullish"
    assert result["ema_fast"] > result["ema_slow"]
    assert result["atr_pct"] > 0
    assert "patterns" in result


def test_analysis_needs_enough_closed_candles():
    result = analyze_ohlcv(candles(count=5), timeframe="5m")
    assert result["status"] == "insufficient_history"
