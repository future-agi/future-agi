# Hosted environment Run and rerun semantics

**Status: In-place reruns are deferred.** This document describes the intended behavior, not the current rerun implementation.

## Start a Run

After environment authoring and validation, the user selects scenarios and a trial count, then clicks **Run simulation**. This creates a new Run with its own execution identity and one call row per selected scenario × trial. Previously created Runs and their results remain available for comparison.

## Rerun calls within a Run

**Rerun** acts on an existing Run. From its detail page, the user may rerun every call or select particular trial rows. The Run identity and call-row IDs stay the same; rerun does not create a new Run in the history list. Only selected rows are cleared and re-executed against the saved, validated environment. Their transcripts, recordings, metrics, evaluations, statuses, and errors are replaced by the new results; unselected rows remain unchanged. Selecting two trials of the same scenario reruns **both** rows, and selecting different numbers of trials across scenarios is allowed because the selection is by call-row ID, not by one shared trial count.

A rerun must start a fresh sandbox attempt with isolated conversation/state, reject an overlapping rerun while the Run is active, and distinguish a retry of the same request from a deliberate new rerun. Progress and Run totals must reflect the selected pending rows plus unaffected completed rows. If a rerun fails, selected rows show that failure rather than silently reverting to the old result. Usage already incurred remains billable and auditable; new execution usage is measured separately, while transport/workflow retries must not duplicate charges.

## Current gap

The existing **Run again** action on the Run detail page submits the saved scenario selection as a **new Run**, contrary to the rerun semantics above. The selected-row **Re-run N** action has no handler. The repository-backed harness branch of the legacy `rerun-calls` API currently accepts only the full suite and dispatches a new saved Run. None of these controls currently performs an in-place hosted rerun; do not present them as such until the backend attempt, receipt replacement, and UI paths are cut over together.
