import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Box, Stack, Typography, Button, CircularProgress, IconButton, Alert,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAvailableEvaluations,
  useAddEvaluation,
  useAddRunEvaluation,
} from "src/api/simulate-environments/environments";
import {
  harnessEnvironmentKey,
  harnessEnvironmentQuery,
} from "src/api/simulate-environments/environment";
import SideDrawer from "../../components/SideDrawer";
import EmptyState from "../../components/EmptyState";
import { EVAL_ENTRY_SHAPE } from "./evalEntry";
import { EvalEntryChips, EvalInputs } from "./evalEntryCells";
import { gradingCountsSentence } from "./gradingCounts";

// §3/§6: an environment runs at most 8 selected evals; the backend 409s past it.
const EVAL_CAP = 8;

// The axios interceptor rejects with the API body plus `statusCode`, so every
// refusal arrives as `detail`: 400 "<name>: not an eval this environment can be
// graded by" / "<name>: needs <keys>, which a <voice|text> run does not produce",
// 409 the cap. Shown exactly as returned — the UI never rewords a refusal and
// never maps a status code to copy of its own.
const addErrorMessage = (error) =>
  error?.detail || error?.message || "Couldn’t add the evaluation. Try again.";

// P27 v1.8 (owner's ruling, 2026-09-23 night — L5 round 4: was cited as v1.7,
// one version behind; §1/P24–P29 are unchanged by v1.8): in run mode the
// picker also lists the evals the environment ALREADY has, as their own group, each with a
// "Grade this run" action that calls the same run-level endpoint (§6) for that
// eval. The heading has to say, in the row itself, why these are here and what
// pressing the button does — the group is otherwise indistinguishable from the
// offer above it.
const BOUND_GROUP_TITLE = "Already on this environment — grade this run's finished calls";

// The Evaluations tab's subtitle. Unchanged by v1.7: from there an add binds
// the eval and grades future calls only (P11).
const ENV_MODE_SUBTITLE =
  "Expand a row to see what fills each input. Adding one grades every call from here on; calls that already finished are left as they are.";

// The run-mode subtitle. P27: before the click it names how many finished calls
// the add will grade — no credit figure, no confirm step, just what is about to
// be graded. `completedCallsCount` comes from the run detail's own stats
// (`RunDetail.jsx` passes `stats.completed`, which is now `kpis.completed_calls`
// for BOTH modalities — P19's number, contract v1.8; NOT `stats.total`, which
// counts every status).
//
// Three branches, not two (round-3 L2 and L3):
//   • not a finite number — still loading, or an older backend that does not
//     send the field yet — name the calls without a count;
//   • exactly 0 — `Number.isFinite(0)` is true, so the old code rendered "the 0
//     finished calls", which is not English. Zero is a real, different answer
//     from "not known yet" and must stay distinguishable, so it gets its own
//     sentence. The Add button is deliberately NOT disabled at zero: the add
//     still binds the eval and every call from here on is graded by it, which
//     is worth doing, so the sentence says so;
//   • n > 0 — name the number. The clause must NOT be restrictive: "the 16
//     finished calls in this run THAT HAVE NO VERDICT FOR IT YET" asserts that
//     all 16 lack one, which the receipt two lines later routinely contradicts
//     ("3 queued, 2 already graded, 1 still being processed — of 16"). P27 asks
//     for the completed count, so the number stays; the claim goes.
// L2 (round 4): "Adding one here…" named only the offer group's action. The
// bound group below adds nothing — its button is "Grade this run" for an eval
// the environment already has. "Each row below" covers both actions without
// claiming the bound group's press also adds something.
const runModeSubtitle = (completedCallsCount) => {
  if (!Number.isFinite(completedCallsCount)) {
    return "Expand a row to see what fills each input. Each row below also grades this run's finished calls — any that already have a verdict for it are left alone.";
  }
  if (completedCallsCount === 0) {
    return "Expand a row to see what fills each input. Nothing is graded yet: no call in this run has finished. An offered row is still added, and every call from here on is graded by it.";
  }
  const calls = completedCallsCount === 1 ? "call" : "calls";
  return `Expand a row to see what fills each input. Each row below also grades this run's ${completedCallsCount} finished ${calls} — any that already have a verdict for it are left alone.`;
};

// Moved out of the component body so `PickerRow`, which both groups render, can
// use them without taking them as props. Neither depends on any prop or state.
const headerCellSx = {
  fontSize: 12, fontWeight: 600, color: "text.secondary",
  py: 1, px: 1, whiteSpace: "nowrap",
  borderBottom: "1px solid", borderColor: "divider",
};
const bodyCellSx = {
  fontSize: 13, py: 0.75, px: 1,
  borderBottom: "1px solid", borderColor: "divider",
};

/**
 * The eval picker.
 *
 * One list, two callers. From the Evaluations tab it adds to the environment
 * (§3) and grades future calls only. Opened from a run with `executionId` it
 * calls the run-level endpoint (§6), which binds the eval exactly as §3 does and
 * then queues this run's finished calls that hold no verdict for it — the 202's
 * five counts are shown as one sentence.
 *
 * Every fact on a row comes from the API entry (§1): `inputs[]` draws the
 * arrows (F1 — the frontend never works out which source fills a key), `source`
 * says Library or Custom and `credits_per_run`/`charges_judge_tokens` build the
 * cost line — "0.5 credits per run" or "0.5 credits per run + judge tokens" on
 * the Evaluations tab; in run mode the same chip reads "0.5 credits per call
 * graded" / "… + judge tokens" instead, since "run" already means the
 * simulation run on that screen (P25). Adding posts `{ name }` only; the
 * platform recomputes the mapping when it binds, so what was shown is what
 * runs.
 */
export default function AddEvaluationDrawer({
  open,
  env,
  executionId,
  completedCallsCount,
  onClose,
}) {
  const envId = env?.id;
  const runMode = Boolean(executionId);
  const { data: evaluations = [], isLoading, isError, error, refetch } =
    useAvailableEvaluations(envId, { enabled: open });
  const [expanded, setExpanded] = useState(null);

  const addToEnvironment = useAddEvaluation();
  const addToRun = useAddRunEvaluation();
  const queryClient = useQueryClient();
  const addMutation = runMode ? addToRun : addToEnvironment;
  const addingName = addMutation.isPending ? addMutation.variables?.name : null;
  // The 202 receipt from the last run-level add, kept on screen until the next
  // click or until the drawer closes — grading is asynchronous, so the counts
  // are all the user gets now.
  // Q4 (owner, 2026-09-23): counts only for v1 — this drawer does not poll the
  // run's per-call table for updated verdicts; the person reloads to see them.
  // L6 (round 3): a 202 with an empty body makes `addToRun.data` the empty
  // string, and `{counts && …}` then renders no receipt at all — bound, queued,
  // and silent. `|| {}` keeps the Alert on screen; `gradingCountsSentence` has
  // its own branch for exactly this — zero buckets, and (Minor-1, fix round 1)
  // an honestly unknown total rather than a false "of 0 calls".
  const counts = runMode && addToRun.isSuccess ? addToRun.data || {} : null;

  // What is already selected (§5). The add updates it from the server's own
  // body (P29) — seeded directly for an environment-mode add, refetched for a
  // run-mode add — so a just-added row flips to "Added" from the server's own
  // answer, never from client-assembled state.
  const detailQuery = useQuery(harnessEnvironmentQuery(envId, { enabled: open }));
  const selected = detailQuery.data?.evaluations?.selected;
  const addedNames = useMemo(
    () => new Set((Array.isArray(selected) ? selected : []).map((e) => e.name)),
    [selected],
  );
  const appliedCount = Array.isArray(selected) ? selected.length : addedNames.size;
  const atCap = appliedCount >= EVAL_CAP;

  // Important-1 (fix round 1): what the offer list above is showing right now,
  // by name — used to keep a just-added eval out of the bound group below.
  const offeredNames = useMemo(() => new Set(evaluations.map((e) => e.name)), [evaluations]);

  // P27 v1.8: the environment's own evals, straight from §5's `selected[]` —
  // P16 entries, which are the whole §1 shape plus `id` and `runnable`, so they
  // render through exactly the same cells as an offered entry. This is the same
  // list the "Added" state already reads; nothing new is fetched. Run mode
  // only: on the Evaluations tab P6's subtraction is the whole answer and this
  // group must not appear.
  //
  // Important-1 (fix round 1, corrected in fix round 2): P6's subtraction is
  // applied when `available` is FETCHED, not continuously — within one open
  // drawer session, adding an eval refetches `selected[]` (so it is now
  // bound) without refetching `available` in the same tick. That is
  // deliberate, not a gap to close: `available` is the offer list's only
  // active observer, so invalidating it here would refetch it immediately and
  // the server's P6 answer would drop the just-added row off the offer, right
  // where "Added" is the only confirmation environment mode has
  // (`useAddEvaluation`/`useAddRunEvaluation` no longer invalidate it — fix
  // round 2). Filtering `selected` by `offeredNames` is therefore the WHOLE
  // fix, not half of one: it keeps that just-added row, still marked "Added"
  // in the offer, from also showing up in this group with a second and
  // different action button. `available` catches up to P6's subtraction only
  // the next time it is genuinely fetched — typically the drawer reopening —
  // at which point the row leaves the offer and this filter simply stops
  // excluding it.
  const boundEntries = useMemo(
    () =>
      runMode && Array.isArray(selected)
        ? selected.filter((e) => !offeredNames.has(e.name))
        : [],
    [runMode, selected, offeredNames],
  );

  // A run view only exists because the environment already has a run test, so
  // §2/P7's sentence (also what the POST's no-run-test 409 returns in lld-3)
  // "Environment has no evaluations until it finishes building" cannot reach
  // this drawer in run mode — a 409 here is the cap of 8, and its `detail` is
  // shown as returned like any other refusal.
  // One call site for both groups and both modes. In run mode this posts §6 —
  // for an offered eval and for one the environment already has alike, which is
  // the point of P27 v1.8: §6 binds nothing it already holds (`add_selected_eval`
  // returns the existing config), skips every call holding a verdict (P20) and
  // bounds a repeat inside ten minutes (P22).
  const add = (name) => {
    const variables = runMode ? { id: envId, executionId, name } : { id: envId, name };
    addMutation.mutate(variables);
  };

  // The drawer stays mounted while it is closed (SideDrawer only hides it), so
  // a stale receipt or error from the last add would otherwise still be on
  // screen the next time it opens — reset both mutations before telling the
  // parent to close. BUT: reset() detaches the query-core observer from an
  // in-flight mutation (mutationObserver.js), which blanks `isSuccess`/`data`
  // even though the mutation keeps running server-side and the grading still
  // happens — so a mid-flight close would otherwise drop the 202 receipt for
  // good (L5). Only reset a mutation that has already settled; a pending one
  // is left alone and is caught by this same check next time the drawer closes
  // (by which point it has had the chance to settle and be shown once).
  const handleClose = () => {
    // Minor-3 (fix round 1): captured before either `reset()` call below.
    // `reset()` only replaces `useMutation`'s result on the mutation
    // observer's NEXT notification, not in place on this one — so reading
    // `addToEnvironment.isSuccess` after `reset()` happens to still work, but
    // that relies on an implementation detail of react-query's
    // `MutationObserver` rather than a documented guarantee. Capturing the
    // flag first makes the order irrelevant.
    const didAdd = addToEnvironment.isSuccess;
    if (!addToRun.isPending) addToRun.reset();
    if (!addToEnvironment.isPending) addToEnvironment.reset();
    // L7 (round 4): `SideDrawer` only hides the component on close — without
    // this, a row left expanded is still expanded the next time the drawer
    // opens (possibly for a different environment/run), and a stale
    // `bound:<name>` key can outlive the bound group it belonged to (harmless:
    // nothing matches it, but there is no reason to carry it forward).
    setExpanded(null);
    // P29 / L11: the environment-level add seeds the detail from its own 201
    // body and does not invalidate, so that a read racing the write cannot undo
    // the seed while the user is looking at it. The server refetch happens
    // here, on the way out, where a stale read costs nothing and the next open
    // starts from the server's list.
    if (didAdd && envId) {
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(envId) });
    }
    onClose?.();
  };

  return (
    <SideDrawer open={open} onClose={handleClose} width={560}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Box sx={{ px: 3, pt: 3, pb: 2, flexShrink: 0 }}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            Add evaluations
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 440 }}>
            {runMode ? runModeSubtitle(completedCallsCount) : ENV_MODE_SUBTITLE}
          </Typography>

          {/* L4 (round 2): the receipt and any error live in this header block,
              not the scrolling list below — with the list scrolled down,
              clicking Add would otherwise render these off-screen and nothing
              visibly changes. */}
          {counts && (
            <Alert severity="success" sx={{ mt: 2, typography: "s3" }}>
              <div>{gradingCountsSentence(counts)}</div>
              {/* L3 (round 2): grading is asynchronous and this drawer does not
                  poll (Q4, owner) — say so, rather than leave the person to
                  guess why the run's per-call table hasn't changed yet. */}
              <div>Reload this run to see the new verdicts.</div>
            </Alert>
          )}
          {/* L1 (round 4): the cap only gates the offer list (§3/§6 for a NEW
              bind) — the bound group below is never gated by it (P27 v1.8,
              see the comment at its render site). When the offer is empty in
              run mode, the only rows on screen are the bound group's, so this
              warning would sit directly above a group its own text
              contradicts. Suppress it there; every other combination
              (offer non-empty, or Evaluations-tab mode) is unaffected. */}
          {atCap && !(runMode && evaluations.length === 0) && (
            <Alert severity="warning" sx={{ mt: 2, typography: "s3" }}>
              This environment already has the maximum {EVAL_CAP} evaluations.
              Remove one before adding another.
            </Alert>
          )}
          {addMutation.isError && (
            <Alert severity="error" sx={{ mt: 2, typography: "s3" }}>
              {addErrorMessage(addMutation.error)}
            </Alert>
          )}
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pb: 3 }}>
          {isLoading ? (
            <Stack alignItems="center" sx={{ py: 6 }}>
              <CircularProgress size={22} />
            </Stack>
          ) : isError ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn’t load evaluations"
              // §2/P7: the list refuses with a sentence of its own — 409
              // "Environment has no evaluations until it finishes building",
              // 404 an invisible environment. Shown exactly as returned; the
              // fallback is only for a failure with no body at all.
              body={error?.detail || "Something went wrong fetching the library. Try again."}
              action={
                <Button variant="outlined" size="small" onClick={() => refetch()}>
                  Retry
                </Button>
              }
            />
          ) : evaluations.length === 0 ? (
            // Important-2 (fix round 2): in run mode, an empty offer next to a
            // non-empty bound group is P27 v1.8's own primary scenario — every
            // eval this environment can be graded by was already added from
            // the Evaluations tab, so §2/P6 subtracts all of them and
            // `available` comes back empty. The old copy ("There's nothing
            // this environment can be graded by right now") is a flat, false
            // statement sitting directly above the rows that contradict it.
            // Swap it for a one-line note that is true of that screen; the
            // Evaluations tab (no `executionId`, no bound group below) keeps
            // the original empty state unchanged.
            //
            // L6 (round 4): `runMode` here is defensive, not the gate —
            // `boundEntries` is already empty outside run mode (`useMemo`
            // above returns `[]` unless `runMode` is true), so this condition
            // reduces to `boundEntries.length > 0`. Left in for readers who
            // don't want to trace back to the memo, and because dropping it
            // changes nothing observable either way.
            runMode && boundEntries.length > 0 ? (
              <Typography sx={{ typography: "s3", color: "text.secondary", py: 1 }}>
                Every eval is already on this environment — grade this run below.
              </Typography>
            ) : (
              <EmptyState
                icon="solar:shield-check-linear"
                title="Nothing left to add"
                // L10 (round 3): P6 says only that an empty list is a valid
                // answer — never why it is empty. "Everything is already applied"
                // is one reason; "the catalogue holds nothing for this
                // environment's modality" and "nobody has authored one" are
                // others, and the client cannot tell them apart. Say what is
                // known and stop there.
                body="There's nothing this environment can be graded by right now."
              />
            )
          ) : (
            <TableContainer>
              <Table size="small" sx={{ tableLayout: "fixed" }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ ...headerCellSx, width: 36 }} />
                    <TableCell sx={{ ...headerCellSx, width: 72 }} />
                    <TableCell sx={headerCellSx}>Evaluation</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {evaluations.map((item) => {
                    const added = addedNames.has(item.name);
                    return (
                      <PickerRow
                        key={item.name}
                        entry={item}
                        runMode={runMode}
                        isExpanded={expanded === item.name}
                        onToggle={() => setExpanded(expanded === item.name ? null : item.name)}
                        actionLabel={added ? "Added" : "Add"}
                        actionWidth={72}
                        muted={added}
                        disabled={added || atCap || addMutation.isPending}
                        busy={addingName === item.name}
                        onAction={() => add(item.name)}
                      />
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}

          {/* P27 v1.8 (owner's ruling, 2026-09-23 night; round-3 M3): in run
              mode the picker also lists the evals the environment already has,
              each with "Grade this run". §2 subtracts them from `available`
              (P6) — correct for the Evaluations tab, wrong here: a run that
              finished before an eval was bound can only be graded for it from
              this group, and without it §6's whole backfill path, and P22's own
              repeat scenario, are unreachable from the UI.

              The button posts to the same run-level endpoint the offer rows
              post to, with the same `{name}` body. The backend binds nothing
              for a name it already holds (`add_selected_eval`'s idempotency
              scan runs before the cap check), never re-grades a call that holds
              a verdict (P20), and queues nothing new for a call stamped within
              ten minutes (P22) — so pressing it twice is safe, and it is what
              produces the `skipped_existing` / `skipped_in_flight` counts the
              receipt renders.

              The 8-eval cap does NOT gate this group: nothing new is bound, and
              TH-8046 pins that a full environment can still grade a run with one
              of its own evals. Only a mutation already in flight disables it.

              Minor-4 (fix round 1): deliberately rendered outside the
              isLoading/isError/empty/list branches above, so it is neither
              hidden under the offer list's spinner nor its "Couldn't load
              evaluations" state. This group's data is `selected[]` from the
              environment-detail query, not `useAvailableEvaluations` — a slow
              or failed offer fetch says nothing about whether the evals this
              environment already has are known, and hiding the run's only §6
              control for those evals over an unrelated failure would remove
              real functionality for no reason tied to it. Pinned by "renders
              the bound group even while the offer list is still loading" and
              "… even when the offer list fails to load" below.

              L6 (round 4): `runMode` in this condition is defensive, not the
              gate — the authoritative gate is the `boundEntries` memo, which
              already returns `[]` outside run mode. Removing `runMode` here
              does not fail any test for that reason; it stays as
              belt-and-braces for a reader who has not traced the memo. */}
          {runMode && boundEntries.length > 0 && (
            <Box sx={{ mt: 3 }}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: "fontWeightSemiBold",
                  color: "text.secondary",
                  mb: 1,
                }}
              >
                {BOUND_GROUP_TITLE}
              </Typography>
              <TableContainer>
                <Table size="small" sx={{ tableLayout: "fixed" }}>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ ...headerCellSx, width: 36 }} />
                      <TableCell sx={{ ...headerCellSx, width: 112 }} />
                      <TableCell sx={headerCellSx}>Evaluation</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {boundEntries.map((item) => {
                      // Its own expander key: `boundEntries` is now filtered
                      // to exclude any name still showing in the offer list
                      // (Important-1, fix round 1) — the old comment here
                      // claimed P6 made that filter unnecessary, which was
                      // false within one open drawer session (see the filter
                      // above). Either way the two groups share one
                      // `expanded` state, and a prefix keeps their keys from
                      // colliding regardless of what either list contains.
                      const key = `bound:${item.name}`;
                      return (
                        <PickerRow
                          key={key}
                          entry={item}
                          runMode={runMode}
                          isExpanded={expanded === key}
                          onToggle={() => setExpanded(expanded === key ? null : key)}
                          actionLabel="Grade this run"
                          actionWidth={112}
                          muted={false}
                          disabled={addMutation.isPending}
                          busy={addingName === item.name}
                          onAction={() => add(item.name)}
                        />
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

AddEvaluationDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ id: PropTypes.string }),
  executionId: PropTypes.string,
  // Run mode only (P27): the run detail's own completed-call count, named in
  // the pre-click sentence. Omit (or pass a non-finite value) when it isn't
  // known yet — the sentence falls back to naming the calls without a number.
  completedCallsCount: PropTypes.number,
  onClose: PropTypes.func,
};

// One row of either group — the expander, the action button, the name and the
// chips. Both groups render the same entry cells (§1, P24/P25): only the
// button's word and what it does differ. "Add" binds an eval the environment
// does not have (§3, or §6 in run mode); "Grade this run" posts §6 for one it
// already has (P27 v1.8).
function PickerRow({
  entry,
  runMode,
  isExpanded,
  onToggle,
  actionLabel,
  actionWidth,
  muted,
  disabled,
  busy,
  onAction,
}) {
  return (
    <>
      <TableRow
        hover
        onClick={onToggle}
        sx={{
          cursor: "pointer",
          bgcolor: isExpanded ? "action.selected" : "inherit",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <TableCell sx={{ ...bodyCellSx, width: 36, px: 0.5 }}>
          {/* L8 (round 3): the chevron is a real button carrying no text, and
              the row it belongs to has no role, no tabIndex and no state of its
              own — a screen reader announced "button" and nothing else, and
              never whether the row was open. The name and `aria-expanded` go on
              the button, the one element an assistive user can both reach and
              operate; the click still bubbles to the row, which is what
              toggles.

              Minor-2 (fix round 1): "Expand"/"Collapse" alone is the same
              accessible name on every row, in both groups — with the offer
              list and the bound group both rendered, a screen reader announces
              N identical buttons with nothing saying which eval each one opens,
              and a bare `{ name: "Expand" }` query stops resolving to one
              element. The eval's own name makes each row's expander distinct. */}
          <IconButton
            size="small"
            sx={{ p: 0.25 }}
            aria-label={`${isExpanded ? "Collapse" : "Expand"} ${entry.name}`}
            aria-expanded={isExpanded}
          >
            <Iconify
              icon={isExpanded ? "solar:alt-arrow-down-bold" : "solar:alt-arrow-right-bold"}
              width={14}
              sx={{ color: isExpanded ? "primary.main" : "text.disabled" }}
            />
          </IconButton>
        </TableCell>
        <TableCell sx={{ ...bodyCellSx, width: actionWidth, px: 0.5 }}>
          <Button
            size="small"
            variant={muted ? "outlined" : "contained"}
            disabled={disabled}
            onClick={(e) => {
              e.stopPropagation();
              if (!disabled) onAction();
            }}
            startIcon={busy ? <CircularProgress size={12} color="inherit" /> : null}
            sx={{ minWidth: 50, height: 24, fontSize: 11, textTransform: "none", px: 1 }}
          >
            {busy ? "…" : actionLabel}
          </Button>
        </TableCell>
        <TableCell sx={bodyCellSx}>
          <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
              {entry.name}
            </Typography>
            <EvalEntryChips entry={entry} runMode={runMode} />
          </Stack>
        </TableCell>
      </TableRow>

      {isExpanded && (
        <TableRow>
          <TableCell colSpan={3} sx={{ p: 0, borderBottom: "1px solid", borderColor: "divider" }}>
            <EvalDetail entry={entry} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

PickerRow.propTypes = {
  entry: EVAL_ENTRY_SHAPE,
  runMode: PropTypes.bool,
  isExpanded: PropTypes.bool,
  onToggle: PropTypes.func,
  actionLabel: PropTypes.string,
  actionWidth: PropTypes.number,
  muted: PropTypes.bool,
  disabled: PropTypes.bool,
  busy: PropTypes.bool,
  onAction: PropTypes.func,
};

// The expanded panel: the description, and what fills each required input — the
// API's `inputs[]`, read-only, with `label` as the only text for a source (P1).
function EvalDetail({ entry }) {
  return (
    <Box sx={{ p: 2, bgcolor: "action.hover", display: "flex", flexDirection: "column", gap: 1.5 }}>
      {entry.description && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {entry.description}
        </Typography>
      )}
      <EvalInputs entry={entry} heading="READS" />
    </Box>
  );
}

EvalDetail.propTypes = { entry: EVAL_ENTRY_SHAPE };
