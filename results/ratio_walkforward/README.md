# XAUUSD ratio study — exact BOS touch / exact sweep-wick stop

Rules used for this run:

- OANDA midpoint XAUUSD M5 signals and M1 execution
- sweep candle must close back inside the swept swing on the same M5 bar
- entry at the first M1 touch of the opposing swing/BOS level
- initial stop exactly at the sweep wick (`0.0 ATR` buffer)
- trailing stop moves to break-even at +1R, then advances one R per whole R reached
- 0.5% compounded equity risk, both directions, one open gold trade at a time
- no session filter, transaction costs, or unspecified volatility filter

## Proper walk-forward result

The grid was ranked on 2015–2022 only. Only the top 20 training candidates were then scored
on 2023–2025.

| Objective | Ratio | Validation trades | Win rate | Average R | Return |
|---|---:|---:|---:|---:|---:|
| Highest validation return | (13,3) | 1,047 | 28.46% | +0.1156R | +77.69% |
| Highest validation win rate / average R | (18,6) | 767 | 28.68% | +0.1330R | +62.93% |
| Second-highest validation return | (6,5) | 1,406 | 26.67% | +0.0839R | +73.74% |
| Highest training return | (6,4) | 1,442 | 27.12% | +0.0804R | +71.50% |

`(13,3)` is the return-first walk-forward choice. `(18,6)` is the risk-efficiency/parity
choice and trades less often. The full-period ranking below is descriptive and was not used
to select candidates.

## Full 2015–2025 descriptive leaders

| Metric | Ratio | Trades | Win rate | Average R | Return |
|---|---:|---:|---:|---:|---:|
| Highest return | (13,3) | 3,835 | 27.01% | +0.0728R | +262.13% |
| Highest win rate | (20,10) | 2,288 | 27.80% | +0.0826R | +141.44% |
| Highest average R | (18,6) | 2,789 | 27.72% | +0.0832R | +195.41% |

## Video-parity check (January 2016–December 2025)

The video screenshot covers January 2016–May 2026, so this is not an identical date window.

| Result | Risk | Trades | Win rate | Average R | Return | Max DD | Return/DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current `(18,6)` | 0.5% | 2,521 | 27.65% | +0.0837R | +168.34% | 17.57% | 9.58 |
| Current `(18,6)` | 1.0% | 2,521 | 27.65% | +0.0837R | +530.31% | 32.48% | 16.33 |
| Video XAUUSD screenshot | 1.0% baseline | 2,588 | not shown | not shown | +477.49% | 41.69% | 11.45 |

Remaining sources of non-parity include the missing January–May 2026 data, unknown source
data/feed and spread conventions, the undisclosed swing ratio, the video's unspecified
volatility filter, its small stop buffer "beyond" the wick, and transaction-cost assumptions.
