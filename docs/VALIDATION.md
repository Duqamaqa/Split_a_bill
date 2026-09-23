# Validation and evidence

## Baseline checked on 23 September 2026

- Application revision: `189462a7ec46711cf617028ca659bfa79cc8a0d8`.
- Environment: macOS, Python 3.13.12, isolated virtual environment.
- Dependencies installed from `requirements.txt`.
- Command: `python -m unittest discover -s tests -v`.
- Result: **10 tests passed; no failures or skips.**

The tests use fixture settings and local logic; no Telegram message or real database operation was performed. The resolved environment included aiogram 3.31.0, FastAPI 0.141.1, psycopg 3.3.6, and pydantic-settings 2.15.0. Dependency ranges may resolve differently in future runs.

## Covered behavior

- Default and explicit currencies, decimal/comma amount parsing, and invalid extra input.
- Positive/negative balance labels.
- Base URL normalization and database URL fallback.
- Matching and missing webhook-secret behavior.

## Not covered by these tests

- SQL transaction correctness and concurrent debt approval.
- Exactly-once processing or recovery after partial failures.
- Hosted endpoint availability or a real Telegram conversation.
- A security audit or complete currency/accounting validation.

The [test workflow](../.github/workflows/tests.yml) reports ongoing results for each commit. Its badge represents the covered test suite, not production readiness.
