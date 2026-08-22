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

## OpenCode Go catalog and limits

The independent OpenCode Go catalog is pinned as `opencode-go-2026-08-21` with
an effective instant and source URL. Remote model discovery may check
`/zen/go/v1/models`, but it cannot change prices or expand the fixed reviewer
model enum. Catalog updates require a code change, fixed-value tests, and manual
review; historical rows retain their recorded catalog version.

The subscription ledger enforces shared rolling virtual-USD windows of $12 per
5 hours, $30 per 7 days, and $60 per 30 days. It also tracks monthly included
usage caps of $15 for GLM 5.3, Kimi K3, and DeepSeek V4 Pro, and $30 for
DeepSeek V4 Flash. Effective monthly remaining is the lesser of shared monthly
remaining and the selected model's remaining cap. Shared usage is aggregated
once per explicit `(account_alias, subscription_id)`, never once per model.

DeepSeek Go rates switch to peak during UTC `[01:00, 04:00)` and
`[06:00, 10:00)`. Input, output, cache-read, and cache-write are preserved as
separate components. A missing official component price is represented as
unsupported (`null`) and cannot silently become zero. Provider-reported usage
or cost remains separate from local estimates; the local allowance view is
marked estimated when an authoritative provider balance is unavailable.

Official reference: <https://opencode.ai/docs/go/>.
