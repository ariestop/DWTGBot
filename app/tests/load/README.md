# Load tests — `app/tests/load/`

Executable counterparts of the recipes in
[`docs/37-load-and-capacity.md`](../../../docs/37-load-and-capacity.md) §6.
Use these to (a) validate capacity math before a `§8` scale-up step
and (b) benchmark the effect of a config change. Record results in
`docs/tests/load-history.md`.

> ⚠ These are **internal, staging-only** tests. They enqueue real
> jobs through `/internal/test/enqueue`, which means they hit
> YouTube / Instagram — use short, stable clips and keep rates low.
> Do **not** run these against production or with high concurrency
> against real upstream platforms (see §11 row 7 of
> [`37-load-and-capacity.md`](../../../docs/37-load-and-capacity.md)).

## Prerequisites

| Requirement | Why |
|---|---|
| `APP_ENV=development` (or `staging`) | `/internal/test/enqueue` is refused in prod (`Settings.validate_runtime`). |
| `INTERNAL_TEST_TOKEN` set + sent as `X-Internal-Test-Token` | The test endpoint is 404 without it. |
| API reachable on `http://localhost:8080` (or override `API_URL`) | curl target. |
| Redis reachable on `localhost:6379` (optional) | Only `concurrency.sh` uses it to wait for the queue to drain. |
| A running worker stack | Otherwise jobs just pile up in Redis. |

All scripts read their knobs from env vars; defaults match the §6 doc.

## Files

| File | Source recipe | What it does |
|---|---|---|
| `smoke.sh` | §6.1 | 100 jobs serially, same URL — read SLOs, compute observed `jobs/h`. |
| `concurrency.sh` | §6.2 | Ramp concurrency `1 → 2 → 4 → 8 → 16`, drain between steps. |
| `mix.sh` | §6.3 | Weighted small / medium / large URL mix. Requires your own URLs. |
| `locustfile.py` | — | Locust-based alternative for open-model load with percentile reporting. |

## Quick start

```bash
export INTERNAL_TEST_TOKEN=<whatever is in .env>
export API_URL=${API_URL:-http://localhost:8080}

# §6.1 smoke
bash app/tests/load/smoke.sh

# §6.2 concurrency ramp
bash app/tests/load/concurrency.sh

# §6.3 file-size mix (set 3 URLs first)
URL_SMALL=... URL_MEDIUM=... URL_LARGE=... \
  bash app/tests/load/mix.sh

# Locust open-model (10 users ramping to 20, 5 min)
# `pip install locust` first — intentionally NOT in requirements/dev.txt
# to keep the baseline image lean; L11 tests are opt-in.
locust -f app/tests/load/locustfile.py --headless \
    -u 20 -r 2 -t 5m --host "$API_URL"
```

## Metrics to collect per run

Record rows in `docs/tests/load-history.md` with this header (from
`docs/37-load-and-capacity.md` §6.4):

```
| Test | Date | Profile | Concurrency | jobs/h | A4 p95 | A5 p95 | Failures | Bottleneck |
```

A run that doesn't produce a row was a waste of time.
