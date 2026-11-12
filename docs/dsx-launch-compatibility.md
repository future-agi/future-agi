# DSX launch compatibility

This unreleased source contract retains existing capabilities while holding DSX additions. It is not deployment acceptance or completed browser coverage.

## Existing capabilities

Upload a file, copy from a dataset, both synthetic dataset routes and Configure Synthetic Data remain available under their existing permission and capability rules. The dataset switcher and real assistant quick actions remain available. Account recovery, MFA and personal-key access are unchanged.

Header Dataset settings retains name editing, scoped reads, validation, cancellation and viewer restrictions. Only DSX description/tags controls and the DSX list Edit details entry are held. Name-only saves omit stored metadata instead of clearing it.

Grid Download and its CSV option remain available. Only JSON, JSONL, Parquet and training formats follow the advanced export gate. An old non-CSV selection falls back to CSV if the gate closes. Existing CSV reads, row/filter selection, version/split capability checks, generated columns and metadata controls are not changed by this format gate. Results export is unchanged.

Legacy eval Groups routes, group creation, creation name/output/pass-threshold controls, editor headers, numbered version reads, Save version and numbered/default selection remain. Only the DSX Latest choice is gated in the saved-eval picker. Stored Latest pins remain represented and are never cleared merely because new selection is held. Decision tree direct mounts and tabs follow the same DSX gate. Existing Remote scorer policy is unchanged.

## DSX holds

Census-(B) holds folder organization, dataset splits, extended dataset settings, experiment split scope and Decision tree UI despite historical claims. The prototype-only version-comparison starter and developer-only gallery entry are removed. Claims and served backend APIs are unchanged; a UI hold is not server authorization.

## Test opt-in and known coverage gaps

There is one opt-in, VITE_DSX_CENSUS_B_TEST=true, which can remove only the five census holds, not claims, parent gates, experimentsBasic or permissions. It is compiled out of every application build by the false constant in vite.config.js. Only the isolated Vitest source profile compiles the constant true. Tests retain launch mode and the real-backend branch. A separate launch-bundle assertion test builds the real gate with the opt-in value true and fails if any hold can be bypassed.

DATA-E2E-020, DATA-E2E-004, DATA-E2E-008, EXP-E2E-006, EVAL-E2E-072 and EVAL-E2E-073 are KNOWN-NOT-RUN, not passing. No qualified two-profile browser harness exists for this unit-only opt-in. Their positive lifecycles and all assertions are unchanged; absence-only runs cannot replace them. They require a separately reviewed real-backend browser profile and default-hidden companions, or a main-authorized surface ON decision. All execution, launch-bundle inertness, fallibility and independent review receipts remain outstanding.

Five upload flows receive only the rendered-label selector correction in a separate planned commit. No flow annotation, assertion, timeout or generated catalog is hand-edited. Any FLOWS.md regeneration is cloud-only.
