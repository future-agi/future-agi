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
import { EVALS_COPY } from "./evals.constants";
import { EvalEntryChips, EvalInputs } from "./evalEntryCells";
import { gradingCountsSentence } from "./gradingCounts";
import { refusalText } from "./refusalText";

// An environment runs at most 8 selected evals; the backend 409s past it.
const EVAL_CAP = 8;

// Refusals are shown exactly as returned — the UI never rewords one and never
// maps a status code to copy of its own. `refusalText` is the single reader of
// the body's several possible message fields.
const ADD_FALLBACK = "Couldn’t add the evaluation. Try again.";
// The offer list refuses with a sentence of its own — 409 "Environment has no
// evaluations until it finishes building", 404 an invisible environment.
const AVAILABLE_FALLBACK = "Something went wrong fetching the library. Try again.";
// The environment detail is what says which evals are already applied. When
// that read fails the drawer knows nothing about them, which is a different
// answer from "there are none". The Evaluations tab shows the same sentence
// for the same failed read, so both take it from the shared copy rather than
// writing it out twice.
const DETAIL_FALLBACK = EVALS_COPY.addedError;

// In run mode the picker also lists the evals the environment ALREADY has, as
// their own group, each with a "Grade this run" action that calls the same
// run-level endpoint for that eval. The heading has to say, in the row
// itself, why these are here and what pressing the button does — the group is
// otherwise indistinguishable from the offer above it.
const BOUND_GROUP_TITLE = "Already on this environment: grade this run's finished calls";

// The Evaluations tab's subtitle: from there an add binds the eval and grades
// future calls only.
const ENV_MODE_SUBTITLE =
  "Expand a row to see what fills each input. Adding one grades every call from here on; calls that already finished are left as they are.";

// The run-mode subtitle. Before the click it names how many finished calls the
// add will grade — no credit figure, no confirm step, just what is about to be
// graded. `completedCallsCount` comes from the run detail's own stats
// (`RunDetail.jsx` passes `stats.completed`, i.e. `kpis.completed_calls`, for
// both modalities — NOT `stats.total`, which counts every status).
//
// Three branches, not two:
//   • not a finite number — still loading, or an older backend that does not
//     send the field yet — name the calls without a count;
//   • exactly 0 — `Number.isFinite(0)` is true, so naively this would render
//     "the 0 finished calls", which is not English. Zero is a real, different
//     answer from "not known yet" and must stay distinguishable, so it gets
//     its own sentence. The Add button is deliberately NOT disabled at zero:
//     the add still binds the eval and every call from here on is graded by
//     it, which is worth doing, so the sentence says so;
//   • n > 0 — name the number. The clause must NOT be restrictive: "the 16
//     finished calls in this run THAT HAVE NO VERDICT FOR IT YET" asserts that
//     all 16 lack one, which the receipt two lines later routinely contradicts
//     ("3 queued, 2 already graded, 1 still being processed — of 16"). The
//     completed count stays; the claim goes.
// "Adding one here…" would name only the offer group's action — the bound
// group below adds nothing, its button is "Grade this run" for an eval the
// environment already has. "Each row below" covers both without claiming the
// bound group's press also adds something.
const runModeSubtitle = (completedCallsCount) => {
  if (!Number.isFinite(completedCallsCount)) {
    return "Expand a row to see what fills each input. Each row below also grades this run's finished calls. Any that already have a verdict for it are left alone.";
  }
  if (completedCallsCount === 0) {
    return "Expand a row to see what fills each input. Nothing is graded yet: no call in this run has finished. An offered row is still added, and every call from here on is graded by it.";
  }
  const calls = completedCallsCount === 1 ? "call" : "calls";
  return `Expand a row to see what fills each input. Each row below also grades this run's ${completedCallsCount} finished ${calls}. Any that already have a verdict for it are left alone.`;
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
 * and grades future calls only. Opened from a run with `executionId` it calls
 * the run-level endpoint, which binds the eval exactly as the environment-level
 * add does and then queues this run's finished calls that hold no verdict for
 * it — the 202's five counts are shown as one sentence.
 *
 * Every fact on a row comes from the API entry: `inputs[]` draws the
 * arrows (the frontend never works out which source fills a key), `source`
 * says Library or Custom and `credits_per_run`/`charges_judge_tokens` build the
 * cost line — "0.5 credits per run" or "0.5 credits per run + judge tokens" on
 * the Evaluations tab; in run mode the same chip reads "0.5 credits per call
 * graded" / "… + judge tokens" instead, since "run" already means the
 * simulation run on that screen. Adding posts `{ name }` only; the platform
 * recomputes the mapping when it binds, so what was shown is what runs.
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
  // The 202 receipt from the last run-level add, kept on screen until the
  // next click or until the drawer closes — grading is asynchronous, so the
  // counts are all the user gets now; this drawer doesn't poll for updated
  // verdicts. A 202 with an empty body makes `addToRun.data` the empty
  // string, so `|| {}` keeps the Alert on screen; `gradingCountsSentence` has
  // its own branch for zero buckets and an honestly unknown total.
  const counts = runMode && addToRun.isSuccess ? addToRun.data || {} : null;

  // What the environment already has. An environment-level add seeds this
  // list straight from its own response body, so the row flips to "Added"
  // from the server's own answer rather than from client-assembled state. A
  // run-level add has no such body to seed from: its confirmation is the
  // receipt below, and this list catches up when the drawer closes.
  const detailQuery = useQuery(harnessEnvironmentQuery(envId, { enabled: open }));
  const selected = detailQuery.data?.evaluations?.selected;
  const addedNames = useMemo(
    () => new Set((Array.isArray(selected) ? selected : []).map((e) => e.name)),
    [selected],
  );
  // How many the environment already has, from the server's own list and
  // nothing else. While that read is in flight or has failed the count is
  // unknown — not zero, and not the client store's idea of it — so the cap
  // cannot be asserted either way and the warning below stays off.
  const appliedCount = Array.isArray(selected) ? selected.length : null;
  const atCap = appliedCount != null && appliedCount >= EVAL_CAP;

  // What the offer list above is showing right now, by name — used to keep
  // a just-added eval out of the bound group below.
  const offeredNames = useMemo(() => new Set(evaluations.map((e) => e.name)), [evaluations]);

  // The environment's own evals, straight from the detail's `selected[]` — the
  // whole catalogue-entry shape plus `id` and `runnable`, so they render
  // through exactly the same cells as an offered entry. This is the same list
  // the "Added" state already reads; nothing new is fetched. Run mode only: on
  // the Evaluations tab the catalogue subtraction is the whole answer and this
  // group must not appear.
  //
  // That subtraction only applies when `available` is FETCHED, not
  // continuously, and neither list is refetched while the drawer is open. So
  // within one open session `selected[]` and `available` can both name the
  // same eval — one because it was just bound by the environment-level add's
  // own 201 body, the other because the offer list has not been asked again.
  // Filtering `selected` by `offeredNames` is therefore the WHOLE fix: it
  // keeps that just-added row, still marked "Added" in the offer, from also
  // showing up in this group with a second, different action button. Both
  // lists catch up the next time they are genuinely fetched — typically the
  // drawer reopening.
  const boundEntries = useMemo(
    () =>
      runMode && Array.isArray(selected)
        ? selected.filter((e) => !offeredNames.has(e.name))
        : [],
    [runMode, selected, offeredNames],
  );

  // A run view only exists because the environment already has a run test,
  // so the "no evaluations until it finishes building" refusal can't reach
  // this drawer in run mode — a 409 here is the cap of 8, shown as returned
  // like any other refusal.
  // One call site for both groups and both modes. In run mode this posts the
  // run-level add for an offered eval and for one the environment already has
  // alike: it binds nothing it already holds, skips every call that already has a
  // verdict, and dedupes a repeat within a short window.
  const add = (name) => {
    const variables = runMode ? { id: envId, executionId, name } : { id: envId, name };
    addMutation.mutate(variables);
  };

  // Both reads can be down at once, and there is one Retry on screen for
  // them. Retrying only the one whose message is showing leaves the other
  // failed, so the press looks like it did nothing — retry whichever failed.
  const retryFailedReads = () => {
    if (isError) refetch();
    if (detailQuery.isError) detailQuery.refetch();
  };

  // The drawer stays mounted while closed (SideDrawer only hides it), so a
  // stale receipt or error would otherwise still be on screen next open —
  // reset both mutations before closing. But `reset()` detaches the observer
  // from an in-flight mutation, blanking `isSuccess`/`data` even though
  // grading keeps running server-side — so only a mutation that has already
  // settled is reset; a pending one is left alone and caught by this same
  // check next time the drawer closes.
  const handleClose = () => {
    // Captured before either `reset()` call: reading `isSuccess` after
    // `reset()` happens to still work today, but that's an implementation
    // detail, not a guarantee — capturing first makes the order irrelevant.
    const didAdd = addToEnvironment.isSuccess || addToRun.isSuccess;
    if (!addToRun.isPending) addToRun.reset();
    if (!addToEnvironment.isPending) addToEnvironment.reset();
    // `SideDrawer` only hides the component on close — without this, a row
    // left expanded stays expanded next open, possibly for a different
    // environment/run.
    setExpanded(null);
    // Neither add path refetches while the drawer is open — a racing read
    // would pull rows out from under the click that caused them. The refetch
    // for both happens here, on the way out, where a stale read costs
    // nothing.
    //
    // An add still in flight at this moment has no success to read yet, so
    // this close does not refetch for it and the applied list stays stale
    // until the next one. That is the same deal the `reset()` skip above
    // makes: a mutation that settles after the drawer is closed is picked up
    // the next time the drawer closes, and until then nothing on screen is
    // showing the list it would have refreshed.
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

          {/* The receipt and any error live in this header block, not the
              scrolling list below — otherwise, scrolled down, clicking Add
              would render these off-screen with nothing visibly changing. */}
          {counts && (
            <Alert severity="success" sx={{ mt: 2, typography: "s3" }}>
              <div>{gradingCountsSentence(counts)}</div>
              {/* Grading is asynchronous and this drawer doesn't poll — say
                  so, rather than leave the person to guess why the run's
                  per-call table hasn't changed yet. */}
              <div>Reload this run to see the new verdicts.</div>
            </Alert>
          )}
          {/* The cap only gates the offer list (a new bind) — the bound
              group below is never gated by it. When the offer is empty in
              run mode, the only rows on screen are the bound group's, so
              this warning would sit above a group its own text contradicts
              — suppress it there. */}
          {atCap && !(runMode && evaluations.length === 0) && (
            <Alert severity="warning" sx={{ mt: 2, typography: "s3" }}>
              This environment already has the maximum {EVAL_CAP} evaluations.
              Remove one before adding another.
            </Alert>
          )}
          {addMutation.isError && (
            <Alert severity="error" sx={{ mt: 2, typography: "s3" }}>
              {refusalText(addMutation.error, ADD_FALLBACK)}
            </Alert>
          )}
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pb: 3 }}>
          {/* Two sources feed this area, and each one's failure may hide only
              what it alone can answer.

              The offer list IS the rows, so its failure replaces them. The
              environment detail says only which of those rows are already
              applied — while it is unknown or unavailable every row stays
              addable, with no false "Added" mark and no cap asserted, so
              neither a pending nor a failed read of it takes the rows away.

              What the detail does gate is the empty state: "Nothing left to
              add — there's nothing this environment can be graded by right
              now" is a confident claim about what the environment already
              has, and a read that hasn't answered — pending or failed —
              cannot support it. So the detail's pending and error states are
              both branched on where the offer list is empty. */}
          {isLoading ? (
            <Stack alignItems="center" sx={{ py: 6 }}>
              <CircularProgress size={22} />
            </Stack>
          ) : isError ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn’t load evaluations"
              // Shown exactly as returned; the fallback is only for a
              // failure with no body at all.
              body={refusalText(error, AVAILABLE_FALLBACK)}
              action={
                <Button variant="outlined" size="small" onClick={retryFailedReads}>
                  Retry
                </Button>
              }
            />
          ) : evaluations.length === 0 ? (
            detailQuery.isPending ? (
              <Stack alignItems="center" sx={{ py: 6 }}>
                <CircularProgress size={22} />
              </Stack>
            ) : detailQuery.isError ? (
              <EmptyState
                icon="solar:danger-triangle-linear"
                title="Couldn’t load evaluations"
                body={refusalText(detailQuery.error, DETAIL_FALLBACK)}
                action={
                  <Button variant="outlined" size="small" onClick={retryFailedReads}>
                    Retry
                  </Button>
                }
              />
            ) : (
              // In run mode, an empty offer next to a non-empty bound group is
              // the common case: every eval this environment can be graded by
              // was already added from the Evaluations tab, so `available`
              // comes back empty. The default empty state ("There's nothing
              // this environment can be graded by right now") would sit
              // directly above rows that contradict it, so swap in a note
              // that's true of this screen. The Evaluations tab (no
              // `executionId`, no bound group below) keeps the original empty
              // state unchanged.
              //
              // `runMode` here is defensive, not the gate — `boundEntries` is
              // already empty outside run mode, so this condition reduces to
              // `boundEntries.length > 0`. Left in for readers who haven't
              // traced the memo.
              (runMode && boundEntries.length > 0 ? (
                <Typography sx={{ typography: "s3", color: "text.secondary", py: 1 }}>
                  Every eval is already on this environment. Grade this run below.
                </Typography>
              ) : (
                <EmptyState
                  icon="solar:shield-check-linear"
                  title="Nothing left to add"
                  // An empty list is a valid answer but never says why it's
                  // empty — everything already applied, nothing in the
                  // catalogue for this modality, or nobody has authored one —
                  // and the client can't tell them apart. Say what is known
                  // and stop there.
                  body="There's nothing this environment can be graded by right now."
                />
              ))
            )
          ) : (
            <>
              {/* The applied list only says which of these rows are already
                  on the environment — the rows themselves came from the
                  offer list, which answered. So a failed read of it doesn't
                  replace the table, just flags itself above it with the same
                  Retry the two EmptyStates use. */}
              {detailQuery.isError && evaluations.length > 0 && (
                <Alert
                  severity="warning"
                  sx={{ mb: 2, typography: "s3" }}
                  action={
                    <Button size="small" onClick={retryFailedReads}>
                      Retry
                    </Button>
                  }
                >
                  {refusalText(detailQuery.error, DETAIL_FALLBACK)}
                </Alert>
              )}
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
            </>
          )}

          {/* In run mode the picker also lists the evals the environment
              already has, each with "Grade this run" — a run that finished
              before an eval was bound can only be graded for it from here.

              The button posts to the same run-level endpoint the offer rows
              post to. The backend binds nothing for a name it already holds,
              never re-grades a call that already has a verdict, and dedupes
              a repeat within a short window — so pressing it twice is safe,
              and it's what produces the `skipped_existing` /
              `skipped_in_flight` counts the receipt renders.

              The 8-eval cap does NOT gate this group: nothing new is bound.
              Only a mutation already in flight disables it.

              Rendered outside the isLoading/isError/empty/list branches
              above, so it's neither hidden under the offer list's spinner
              nor its error state — this group's data comes from the
              environment-detail query, not the offer list's, so an
              unrelated failure there shouldn't hide it.

              `runMode` here is defensive, not the gate — `boundEntries` is
              already empty outside run mode. Left in for readers who
              haven't traced the memo. */}
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
                      // Its own expander key: the two groups share one
                      // `expanded` state, and a prefix keeps their keys from
                      // colliding regardless of what either list contains.
                      const key = `bound:${item.name}`;
                      return (
                        <PickerRow
                          key={key}
                          entry={item}
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
  // Run mode only: the run detail's own completed-call count, named in the
  // pre-click sentence. Omit (or pass a non-finite value) when it isn't known
  // yet — the sentence falls back to naming the calls without a number.
  completedCallsCount: PropTypes.number,
  onClose: PropTypes.func,
};

// One row of either group — the expander, the action button, the name and the
// chips. Both groups render the same entry cells: only the button's word
// and what it does differ. "Add" binds an eval the environment does not have
// (the run-level endpoint in run mode); "Grade this run" posts that same
// endpoint for one it already has.
function PickerRow({
  entry,
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
          {/* The chevron is a real button carrying no text; the row itself has
              no role, no tabIndex and no state of its own, so the name and
              `aria-expanded` go on the button — the one element an assistive
              user can reach and operate. The click still bubbles to the row,
              which is what toggles.

              "Expand"/"Collapse" alone would be the same accessible name on
              every row across both groups — the eval's own name makes each
              row's expander distinct. */}
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
            <EvalEntryChips entry={entry} />
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
  isExpanded: PropTypes.bool,
  onToggle: PropTypes.func,
  actionLabel: PropTypes.string,
  actionWidth: PropTypes.number,
  muted: PropTypes.bool,
  disabled: PropTypes.bool,
  busy: PropTypes.bool,
  onAction: PropTypes.func,
};

// The expanded panel: the description, and what fills each required input —
// the API's `inputs[]`, read-only, with `label` as the only text for a source.
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
