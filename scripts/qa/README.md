# `scripts/qa` — manually run QA probes

Standalone probes and regression checks that are **run by hand**, not by CI.

Nothing in this directory is collected by the backend pytest suite, referenced by
a workflow in `.github/workflows/`, or wired into any `package.json` script. If
you change code these files cover, you have to run them yourself — no automated
gate will tell you that you broke one.

Most of them execute application definitions offline: they compile the real
source with `ast`/`runpy` and stub only the boundaries (ORM, cache, crypto,
network), so they need no database, no Django bootstrap and no running stack.
That is also why they must be run directly rather than through the backend
pytest bootstrap, which would import the app for real.

Run every command from the repository root.

## Files added by the observed-property-catalog branch

### `test_workspace_default_preferences.py`
Default-workspace resolver control flow in `futureagi/accounts/authentication.py`.

```bash
python3 scripts/qa/test_workspace_default_preferences.py
```

No third-party dependencies. Measured on 2026-09-10: `Ran 21 tests … OK`.

### `test_axios_authentication.mjs`
Executes `frontend/src/utils/axios.js` unchanged in a VM against real Axios and
an in-memory adapter. No browser, no refresh endpoint, no network.

```bash
node --experimental-vm-modules scripts/qa/test_axios_authentication.mjs
```

Needs `frontend/node_modules` installed, since it resolves Axios through
`frontend/package.json`. Pass a different `package.json` path as the first
argument to test against another local Axios installation. Measured on
2026-09-10: `tests 10 / pass 10 / fail 0`.

### `test_authentication_database.py`
Real DRF dispatch, exception handling, `set_rollback` and the public
error-envelope implementation; ORM, cache and the EE feature exception are
doubles.

```bash
python3 scripts/qa/test_authentication_database.py
```

Unlike the other two this one imports `django` and `cryptography`, so it needs
the backend dependency set (`django==5.1.8`, `cryptography==43.0.3` in
`futureagi/requirements.txt`) on the interpreter's path. Activate the backend
environment first; a bare system `python3` fails at import.

## Wiring this into CI

Deliberately not done yet. Automating a directory this size is its own change
with its own blast radius, and `test_authentication_database.py` needs the
backend dependency set while the other two do not — so a single job does not
cover them. Until that happens, treat these as pre-merge checks you run by hand
when you touch the code they cover.
