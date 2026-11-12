# fix(auth): clear in-memory stores on same-tab logout

## Problem
Logging out keeps the app loaded in the tab, so in-memory Zustand stores keep the previous account's state, and the next account signed in on that tab can see it.
- Only the Falcon store was reset. About 85 stores were not, including traces, alerts, users, evals, error feed, test runs and simulate environments.
- The simulate environments draft also stayed in sessionStorage.
- When the logout request itself failed (expired token, network error), the query cache was not cleared either.

## Change
- This carries #409 by @kvr06-ai unchanged, which resets the stores it names on logout, and completes it.
- Stores are now created through `src/utils/zustand`, a drop-in for zustand's `create()` that registers each store. On logout:
  1. every store returns to its initial state;
  2. persisted stores clear their storage;
  3. the stores' own clean-ups from #409 run.
- A new store is covered automatically, and a test fails if a module creates a store with zustand directly.
- The query cache is cleared in a `finally` block, so a failed logout request still clears it.

Closes #408

## Validation
- A new test finds every store module in `src`, plants state in every exported store, logs out, and checks that no store keeps it. It failed for 84 stores before this change.
- Further tests cover:
  - the persisted draft;
  - the direct-import guard;
  - `AuthProvider` logout with a successful and with a failing logout request.
- The full frontend suite passes (11987 passed, 14 skipped).
- In headless Chromium against a local backend, user A logged out through the workspace menu and user B signed in on the same tab without a reload. Of the 7 stores and storage entries probed for A's data:
  - on dev, all 7 still held it;
  - with #409 alone, 6 did;
  - with this change, none did, and B's dashboard worked normally.
- New E2E flow AUTH-E2E-016 (`e2e/flows/auth/logout-clears-previous-account.spec.ts`), run on production images built from this branch:
  - A signs in through the login form, opens Error Feed from the sidebar and searches for a value only A typed. A logs out through the workspace menu. B signs in on the same tab; a window marker and the request log prove there was no page reload. B then opens Error Feed.
  - It asserts that B's search box is empty, that no Error Feed request of B carries a search value, that no request of B carries another organization, and that every request B sends from the app after signing in carries B's organization. It also checks that none of A's values (search text, email, organization id and name, workspace id) appear in B's page text, API requests or browser storage, and that the token in the tab resolves to B.
  - On the dev frontend it fails: `Error Feed search box after B signs in` expected `""`, received `"e2e-auth5-a-search-…"`, and B's Error Feed request sent `search=e2e-auth5-a-search-…`. With #409 alone it also fails, because the Error Feed store is not one of the stores #409 names. With this change it passes (6 of 6 runs, no retries).

## Limits
- The E2E flow pins one leaking screen (Error Feed search), plus page text, API requests and browser storage. The unit coverage test pins every store.
- A full page reload on logout would also drop module-level caches outside Zustand. This change does not add one, because the invite-retry flow relies on logout not navigating.
- Other tabs are unchanged; they already reload on logout or user change.

AI use: Claude Code (Claude Opus 5.5) wrote the tests and the change and ran the verification; maintainers retain review and merge decisions. This carries #409 by @kvr06-ai unchanged.
E2E: new AUTH-E2E-016
