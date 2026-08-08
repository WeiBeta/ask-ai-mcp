# DeepSeek pricing and audit policy

## Current status

The price snapshot uses the DeepSeek V4 prices published on 2026-08-01:

| Model | Cache-hit input | Cache-miss input | Output |
| --- | ---: | ---: | ---: |
| `deepseek-v4-flash` | CNY 0.02/M | CNY 1/M | CNY 2/M |
| `deepseek-v4-pro` | CNY 0.025/M | CNY 3/M | CNY 6/M |

DeepSeek's official page currently says peak pricing is forthcoming and that
the exact activation time will be announced separately. Until an explicit
effective instant is configured, every request remains on the published base
schedule and the estimator does not double costs.

Official reference:
<https://api-docs.deepseek.com/zh-cn/quick_start/pricing/>

## Peak schedule support

The implementation already understands the announced peak windows: Beijing
time 09:00–12:00 and 14:00–18:00, with start boundaries inclusive and end
boundaries exclusive. When the schedule is active, all three token prices use a
2x multiplier during those windows.

Beijing time is represented as a fixed UTC+8 offset. It does not depend on the
host's selected Windows time zone and needs no external time-zone database.
Pricing uses the instant immediately before the API request is sent, not the
response completion time.

Peak pricing stays disabled while this environment variable is absent:

```text
ASK_AI_MCP_PEAK_PRICING_EFFECTIVE_AT
```

After DeepSeek publishes a formal activation time, set it to an offset-aware
ISO-8601 instant such as `2026-08-05T00:00:00+08:00` and restart both desktop
clients. Never infer or guess this value from the announced daily windows.

## Audit fields

Every new external-call record preserves:

- request-start pricing instant in UTC;
- `standard` or `peak` pricing band;
- applied multiplier and price-schedule version;
- cache-hit, cache-miss, and output unit prices;
- token counts, client identity, model, status, and estimated CNY cost.

The SQLite migration preserves existing history and labels legacy records as
`standard` under `legacy_base`; their already-recorded cost is not recomputed.
`usage_status` reports call counts and estimated cost by client, model, and
pricing band, plus the current Beijing pricing state. It also reports at most
twenty recent lifecycle rows. Provider token counts are whole-request actuals;
specification and candidate field sizes are separately reported as characters
and UTF-8 bytes. Ratios use bytes and are never labelled token ratios.

These values are audit estimates. DeepSeek's account ledger remains the source
of truth for actual deductions.
