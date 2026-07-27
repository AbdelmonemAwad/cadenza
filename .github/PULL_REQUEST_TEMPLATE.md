## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem being solved, not a restatement of the diff. -->

## Checklist

- [ ] `cd backend && ruff check app tests && pytest` passes
- [ ] `cd frontend && npx tsc --noEmit && npm run build` passes
- [ ] New user-facing strings are in `frontend/src/i18n/en.ts` **and** `ar.ts`
- [ ] Comments and docstrings are in English
- [ ] A bug fix includes a test that fails without the fix

## Safety review

Tick only what applies; if any is ticked, explain below.

- [ ] Touches a code path that moves, writes or deletes files
- [ ] Changes a default in `config.py`
- [ ] Alters the duplicate decision weights or thresholds
- [ ] Changes the database schema

<!-- Explanation for anything ticked above: -->
