import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  MenuItem,
  Radio,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { clickable, focusRing } from "../../components/clickable";
import { groupsOf, validateRows } from "../../_mock/scenarioOrg";
import { subGoalsOfScenario } from "../../_mock/envModel";
import { currentEnvVersion } from "../../_mock/versions";
import { currentUser } from "../../_mock/scenarioProvenance";
import {
  REDACTION_RULES,
  STATE_MODES,
  assumptionsOf,
  draftSourcesOf,
  duplicatesOf,
  evidenceKindsOf,
  existingMatchesOf,
  fixtureOptionsOf,
  happenedOf,
  mappingsOf,
  mergePatch,
  redactionsOf,
  reviewOf,
  reviewPatch,
  reviewedDraftOf,
  shouldOf,
} from "../../_mock/traceReview";
import {
  caseIdLineOf,
  coverageNudgeOf,
  importSummaryOf,
  observeHrefOf,
  removeDraftsPatch,
  uncoveredCasesOf,
  waitingDraftsOf,
} from "../../_mock/importReview";

const MONO = "ui-monospace, Menlo, monospace";
const head = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  mb: 0.5,
};
const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const row = {
  px: 1.25,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};
const selSx = {
  typography: "s2",
  minWidth: 200,
  "& .MuiSelect-select": { py: 0.5 },
};
const CHECKBOX_SX = {
  p: 0.25,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};
const plural = (n, one, many = `${one}s`) => (n === 1 ? one : many);

/* A review action — a real button, never text that reads as a label. */
function ActBtn({ onClick, children }) {
  return (
    <Button
      size="small"
      variant="outlined"
      onClick={onClick}
      sx={{ minWidth: 0, py: 0, px: 1, flexShrink: 0 }}
    >
      {children}
    </Button>
  );
}
ActBtn.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

/* One section of a draft's review. */
function Section({ title, children }) {
  return (
    <Box>
      <Typography sx={head}>{title}</Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

/* The one environment-level nudge: what some cases need the environment to
   cover. Never a card per case, never a count of what couldn't be drafted. */
function CoverageNudge({ nudge, onUpdate }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon="solar:globus-linear"
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
        {nudge.line}
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {" "}
          · they&apos;re drafted once it does
        </Box>
      </Typography>
      {onUpdate && (
        <Button
          size="small"
          variant="outlined"
          onClick={onUpdate}
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          Update environment
        </Button>
      )}
    </Stack>
  );
}
CoverageNudge.propTypes = {
  nudge: PropTypes.object.isRequired,
  onUpdate: PropTypes.func,
};

/**
 * Review imported evidence — the one review every import goes through
 * (Observe sessions, traces or calls, an Error Feed issue, uploaded
 * transcripts or datasets). The drafts on the left, each with its source id;
 * the open one's evidence and repairs on the right.
 *
 * Review is compulsory: nothing joins the suite until it's added here, and a
 * draft waits until it's added or removed. Removing is undoable until the
 * drawer closes. Cases the environment can't cover yet are not listed — one
 * line says what the environment is missing and hands it to the builder.
 * "Edit with the builder" focuses the environment's chat on a draft.
 *
 * `importId` reviews one import; without it, every draft that's waiting.
 */
export default function TraceReviewDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  importId = null,
  onApprove,
  approveLabel = null,
  onEditWithBuilder = null,
  onUpdateEnvironment = null,
  onViewInSuite = null,
}) {
  /* Each draft titled for what its record shows — a session that passed
     is kept as a regression, never titled as reproducing a failure. */
  const drafts = useMemo(
    () =>
      waitingDraftsOf(envState, importId).map((d) =>
        reviewedDraftOf(d, env, envState),
      ),
    [env, envState, importId],
  );
  const uncovered = useMemo(
    () => uncoveredCasesOf(envState, importId),
    [envState, importId],
  );
  const nudge = coverageNudgeOf(uncovered);
  const [openId, setOpenId] = useState(null);
  /* The user's own removals, undoable until they leave. */
  const [removed, setRemoved] = useState(() => new Set());
  /* Drafts left out of this add — they keep waiting. */
  const [skipped, setSkipped] = useState(() => new Set());
  const [added, setAdded] = useState(0);
  useEffect(() => {
    if (!open) return;
    setOpenId(null);
    setRemoved(new Set());
    setSkipped(new Set());
    setAdded(0);
  }, [open, importId]);
  /* A title the review corrected is saved, so the scenario joins the suite
     under it. */
  useEffect(() => {
    if (!open) return;
    const fixed = new Map(
      drafts
        .filter(
          (d) =>
            d.title !==
            (envState?.scenarios || []).find((r) => r.id === d.id)?.title,
        )
        .map((d) => [d.id, d]),
    );
    if (!fixed.size) return;
    patch({
      scenarios: (envState?.scenarios || []).map((r) => {
        const d = fixed.get(r.id);
        return d
          ? {
              ...r,
              title: d.title,
              summary: d.summary,
              traceImport: d.traceImport,
            }
          : r;
      }),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, drafts]);

  const kept = drafts.filter((d) => !removed.has(d.id));
  const chosen = kept.filter((d) => !skipped.has(d.id));
  const current = drafts.find((d) => d.id === openId) || kept[0] || null;
  const dupes = duplicatesOf(kept);
  const save = (fields) =>
    current && patch(reviewPatch(envState, current.id, fields));
  const toggleIn = (set, setter, id) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  /* Leaving settles the removals — however the review closes (Close, a
     hand-off to the builder, adding for a fix) — on the newest state, so
     nothing added on the way out is undone. The drafts go; the import's
     history keeps that they were removed. */
  const latest = useRef(null);
  latest.current = { envState, removed, patch };
  useEffect(() => {
    if (!open) return undefined;
    return () => {
      const { envState: es, removed: gone, patch: save } = latest.current;
      const rows = (es?.scenarios || []).filter((r) => gone.has(r.id));
      if (rows.length) save(removeDraftsPatch(es, rows, currentUser().name));
    };
  }, [open]);
  const leave = () => onClose();
  const handOff = (fn) => () => fn();

  if (!open) return <SideDrawer open={false} onClose={onClose} />;
  const isRemoved = current ? removed.has(current.id) : false;
  const sources = current ? draftSourcesOf(current, env, envState) : [];
  const review = current ? reviewOf(current, sources) : null;
  const maps = current ? mappingsOf(current, env, sources) : [];
  const missingState = sources.some((s) => !s.evidence.startingState);
  const hasTools = sources.every((s) => s.evidence.tools);
  const observeHref = current ? observeHrefOf(current) : null;
  /* Everything that was waiting is in the suite now. */
  const allDone = added > 0 && kept.length === 0;
  const summary = importSummaryOf(drafts, uncovered);

  return (
    <SideDrawer open onClose={leave} width={960}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Review imported evidence
            </Typography>
            {summary && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {summary}
              </Typography>
            )}
          </Box>
          <IconButton size="small" aria-label="Close" onClick={leave}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        {nudge && (
          <CoverageNudge
            nudge={nudge}
            onUpdate={
              onUpdateEnvironment
                ? handOff(() => onUpdateEnvironment(nudge))
                : null
            }
          />
        )}

        {allDone ? (
          <Stack
            alignItems="center"
            justifyContent="center"
            spacing={0.75}
            sx={{ flex: 1, minHeight: 0, px: 3, textAlign: "center" }}
          >
            <Iconify
              icon="solar:check-circle-linear"
              width={28}
              sx={{ color: "status.success.fg" }}
            />
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {`${added} drafted ${plural(added, "scenario")} added to the suite`}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {added === 1 ? "It runs" : "They run"} from now on with the rest
              of this environment&apos;s scenarios.
            </Typography>
          </Stack>
        ) : !drafts.length ? (
          <Stack
            alignItems="center"
            justifyContent="center"
            sx={{ flex: 1, minHeight: 0, px: 3, textAlign: "center" }}
          >
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {nudge
                ? "No drafted scenarios are waiting. The cases above are drafted once the environment covers what they need."
                : "Nothing is waiting for your review."}
            </Typography>
          </Stack>
        ) : (
          <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
            {/* The drafts — each with its source id and whether it's in this add. */}
            <Box
              sx={{
                width: 300,
                flexShrink: 0,
                borderRight: "1px solid",
                borderColor: "divider",
                overflow: "auto",
              }}
            >
              {drafts.map((d) => {
                const gone = removed.has(d.id);
                return (
                  <Stack
                    key={d.id}
                    direction="row"
                    alignItems="flex-start"
                    spacing={0.75}
                    {...clickable(() => setOpenId(d.id))}
                    sx={{
                      ...focusRing,
                      px: 1.25,
                      py: 1,
                      cursor: "pointer",
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      bgcolor:
                        current?.id === d.id
                          ? "action.selected"
                          : "transparent",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    {gone ? (
                      <Box sx={{ width: 22, flexShrink: 0 }} />
                    ) : (
                      <Checkbox
                        size="small"
                        checked={!skipped.has(d.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleIn(skipped, setSkipped, d.id)}
                        inputProps={{
                          "aria-label": `Include ${d.title} in this add`,
                        }}
                        sx={CHECKBOX_SX}
                      />
                    )}
                    <Box flex={1} minWidth={0}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s2",
                          fontWeight: 500,
                          color: gone ? "text.disabled" : "text.primary",
                        }}
                      >
                        {d.title}
                      </Typography>
                      {gone ? (
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            Removed
                          </Typography>
                          <Button
                            size="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleIn(removed, setRemoved, d.id);
                            }}
                            sx={{ minWidth: 0, p: 0, typography: "s3" }}
                          >
                            Undo
                          </Button>
                        </Stack>
                      ) : (
                        <Typography
                          noWrap
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            fontFamily: MONO,
                          }}
                        >
                          {caseIdLineOf(d)}
                          {d.generatedVariant ? " · generated variant" : ""}
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                );
              })}
              {dupes.length > 0 && (
                <Box sx={{ px: 1.5, py: 1 }}>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {`${dupes.length} set${dupes.length === 1 ? "" : "s"} of drafts look like the same situation.`}
                  </Typography>
                  {dupes.map((ids) => (
                    <ActBtn
                      key={ids.join()}
                      onClick={() => patch(mergePatch(envState, ids))}
                    >
                      {`Merge ${ids.length} — every source is kept`}
                    </ActBtn>
                  ))}
                </Box>
              )}
            </Box>

            {/* The open draft: its source, the builder, and its review. */}
            <Box sx={{ flex: 1, minWidth: 0, overflow: "auto", p: 2 }}>
              {current && isRemoved && (
                <Stack spacing={1} alignItems="flex-start">
                  <Typography
                    sx={{
                      typography: "s1",
                      fontWeight: 600,
                      color: "text.disabled",
                    }}
                  >
                    {current.title}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    Removed — it won&apos;t be added. Undo brings it back until
                    you close this review.
                  </Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => toggleIn(removed, setRemoved, current.id)}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Undo
                  </Button>
                </Stack>
              )}
              {current && !isRemoved && (
                <Stack spacing={2}>
                  <Stack direction="row" alignItems="flex-start" spacing={1}>
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                        {current.title}
                      </Typography>
                      <Typography
                        component="div"
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {current.generatedVariant
                          ? `Generated variant of ${current.generatedVariant.parentTrace} — not something that happened in production · `
                          : ""}
                        <Box component="span" sx={{ fontFamily: MONO }}>
                          {caseIdLineOf(current)}
                        </Box>
                        {current.traceImport?.merged?.length
                          ? ` · merged with ${current.traceImport.merged.length} more`
                          : ""}
                        {observeHref && (
                          <>
                            {" · "}
                            <Box
                              component={RouterLink}
                              to={observeHref}
                              target="_blank"
                              rel="noopener"
                              sx={{
                                color: "text.secondary",
                                textDecoration: "underline",
                                "&:hover": { color: "text.primary" },
                              }}
                            >
                              Open in Observe
                            </Box>
                          </>
                        )}
                      </Typography>
                    </Box>
                    {onEditWithBuilder && (
                      <Button
                        size="small"
                        variant="outlined"
                        startIcon={
                          <Iconify icon="solar:pen-2-linear" width={14} />
                        }
                        onClick={handOff(() => onEditWithBuilder(current))}
                        sx={{
                          flexShrink: 0,
                          color: "text.primary",
                          borderColor: "divider",
                        }}
                      >
                        Edit with the builder
                      </Button>
                    )}
                    <Button
                      size="small"
                      onClick={() => toggleIn(removed, setRemoved, current.id)}
                      sx={{ flexShrink: 0, color: "text.secondary" }}
                    >
                      Remove
                    </Button>
                  </Stack>

                  {(current.traceReview?.builderEdits || []).length > 0 && (
                    <Section title="Changed with the builder">
                      <Box sx={box}>
                        {current.traceReview.builderEdits.map((e) => (
                          <Typography
                            key={e.at}
                            sx={{ ...row, typography: "s2" }}
                          >
                            {e.ask}
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary", ml: 0.75 }}
                            >
                              {[
                                e.by,
                                new Date(e.at).toLocaleString("en-GB", {
                                  day: "numeric",
                                  month: "short",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                }),
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </Box>
                          </Typography>
                        ))}
                      </Box>
                    </Section>
                  )}

                  <Section title="What the record shows">
                    <ToggleButtonGroup
                      exclusive
                      size="small"
                      value={review.shows}
                      onChange={(_, v) =>
                        v && save({ shows: v, validated: null })
                      }
                    >
                      <ToggleButton value="failure">Failure</ToggleButton>
                      <ToggleButton value="success">Success</ToggleButton>
                      <ToggleButton value="partial">
                        Partial evidence
                      </ToggleButton>
                    </ToggleButtonGroup>
                  </Section>

                  <Section title="Evidence">
                    <Box sx={{ ...box, mb: 0.75 }}>
                      {evidenceKindsOf(sources).map(([k, present]) => (
                        <Stack key={k} direction="row" sx={row}>
                          <Typography sx={{ typography: "s2", flex: 1 }}>
                            {k}
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s3",
                              color: present
                                ? "text.secondary"
                                : "status.warning.fg",
                            }}
                          >
                            {present ? "Present" : "Missing"}
                          </Typography>
                        </Stack>
                      ))}
                    </Box>
                    <Box sx={box}>
                      {sources.map((s) => (
                        <Typography
                          key={s.traceId}
                          sx={{ ...row, typography: "s2" }}
                        >
                          <Box
                            component="span"
                            sx={{ fontFamily: MONO, mr: 1 }}
                          >
                            {s.traceId}
                          </Box>
                          {[
                            s.sourceName && s.sourceKind ? s.sourceName : null,
                            s.agentVersion ? `agent ${s.agentVersion}` : null,
                            s.outcome === "failed"
                              ? "failed"
                              : s.outcome === "passed"
                                ? "passed"
                                : null,
                            s.evidence.transcript ? "transcript" : null,
                            s.evidence.tools ? "tool calls" : null,
                            s.evidence.startingState ? "starting state" : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          {!s.evidence.startingState && (
                            <Box
                              component="span"
                              sx={{ color: "status.warning.fg" }}
                            >
                              {" "}
                              · no starting state
                            </Box>
                          )}
                        </Typography>
                      ))}
                    </Box>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        mt: 0.5,
                      }}
                    >
                      {`Evidence class: ${STATE_MODES[review.stateMode].evidence}.`}
                    </Typography>
                  </Section>

                  {missingState && (
                    <Section title="Starting state — the trace didn't record it">
                      <Typography
                        sx={{
                          typography: "s2",
                          color: "text.secondary",
                          mb: 0.75,
                        }}
                      >
                        {review.stateMode === "synthetic"
                          ? "The records the tools read first weren't recorded, so the draft makes them up and labels them synthetic: the test is not an exact replay."
                          : `${STATE_MODES[review.stateMode].evidence}.`}
                      </Typography>
                      <Stack spacing={0.5}>
                        {["fixture", "replay", "synthetic", "conversation"].map(
                          (m) => {
                            const off = m === "replay" && !hasTools;
                            return (
                              <Stack
                                key={m}
                                direction="row"
                                alignItems="center"
                                spacing={1}
                                {...clickable(
                                  () =>
                                    !off &&
                                    save({ stateMode: m, validated: null }),
                                  {
                                    role: "radio",
                                    checked: review.stateMode === m,
                                  },
                                )}
                                sx={{
                                  ...focusRing,
                                  cursor: off ? "not-allowed" : "pointer",
                                  opacity: off ? 0.5 : 1,
                                }}
                              >
                                <Radio
                                  size="small"
                                  checked={review.stateMode === m}
                                  tabIndex={-1}
                                  disabled={off}
                                  sx={{ p: 0 }}
                                />
                                <Typography sx={{ typography: "s2" }}>
                                  {STATE_MODES[m].label}
                                </Typography>
                                {off && (
                                  <Typography
                                    sx={{
                                      typography: "s3",
                                      color: "text.tertiary",
                                    }}
                                  >
                                    — no tool responses were recorded
                                  </Typography>
                                )}
                              </Stack>
                            );
                          },
                        )}
                      </Stack>
                      {review.stateMode === "fixture" && (
                        <Select
                          size="small"
                          displayEmpty
                          value={review.fixture || ""}
                          onChange={(e) =>
                            save({ fixture: e.target.value, validated: null })
                          }
                          sx={{ ...selSx, mt: 0.75 }}
                        >
                          <MenuItem value="" sx={{ typography: "s2" }}>
                            Pick the seeded record it starts from
                          </MenuItem>
                          {fixtureOptionsOf(env).map((o) => (
                            <MenuItem
                              key={o.id}
                              value={o.id}
                              sx={{ typography: "s2" }}
                            >
                              {o.label}
                            </MenuItem>
                          ))}
                        </Select>
                      )}
                    </Section>
                  )}

                  <Section title="Records, people and tools">
                    <Box sx={box}>
                      {maps.map((m) => (
                        <Stack
                          key={m.id}
                          direction="row"
                          spacing={1.25}
                          alignItems="center"
                          sx={row}
                        >
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              width: 52,
                              flexShrink: 0,
                            }}
                          >
                            {m.kind}
                          </Typography>
                          <Typography
                            sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                          >
                            {m.from}
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s2",
                              color:
                                m.unresolved && !review.mapping[m.id]
                                  ? "status.warning.fg"
                                  : "text.secondary",
                              flex: 1,
                              minWidth: 0,
                            }}
                          >
                            {m.unresolved
                              ? review.mapping[m.id] === "stub"
                                ? "Answered by a stub in the world — labelled on results"
                                : review.mapping[m.id] === "drop"
                                  ? "Left out — the draft doesn't need it"
                                  : "Only exists in production — no world tool"
                              : `→ ${m.to}`}
                          </Typography>
                          {m.unresolved && !review.mapping[m.id] && (
                            <Stack direction="row" spacing={1}>
                              <ActBtn
                                onClick={() =>
                                  save({
                                    mapping: {
                                      ...review.mapping,
                                      [m.id]: "stub",
                                    },
                                  })
                                }
                              >
                                Stub it
                              </ActBtn>
                              <ActBtn
                                onClick={() =>
                                  save({
                                    mapping: {
                                      ...review.mapping,
                                      [m.id]: "drop",
                                    },
                                  })
                                }
                              >
                                Leave out
                              </ActBtn>
                            </Stack>
                          )}
                        </Stack>
                      ))}
                    </Box>
                  </Section>

                  <Section title="Personal data — what replaces it">
                    <Box sx={box}>
                      {redactionsOf(current).map((r) => (
                        <Stack
                          key={r.id}
                          direction="row"
                          spacing={1.25}
                          alignItems="center"
                          sx={row}
                        >
                          <Typography
                            sx={{ typography: "s2", width: 190, flexShrink: 0 }}
                          >{`${r.label} · ${r.n}`}</Typography>
                          <Select
                            size="small"
                            value={review.redaction[r.id] || "fake"}
                            onChange={(e) =>
                              save({
                                redaction: {
                                  ...review.redaction,
                                  [r.id]: e.target.value,
                                },
                              })
                            }
                            sx={{ ...selSx, minWidth: 150 }}
                          >
                            {Object.entries(REDACTION_RULES).map(([k, l]) => (
                              <MenuItem
                                key={k}
                                value={k}
                                disabled={k === "keep" && !r.canKeep}
                                sx={{ typography: "s2" }}
                              >
                                {l}
                              </MenuItem>
                            ))}
                          </Select>
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              fontFamily: MONO,
                            }}
                          >
                            {(review.redaction[r.id] || "fake") === "mask"
                              ? "••••••"
                              : (review.redaction[r.id] || "fake") === "keep"
                                ? "kept as is"
                                : `e.g. ${r.example}`}
                          </Typography>
                        </Stack>
                      ))}
                      {!redactionsOf(current).length && (
                        <Typography
                          sx={{
                            ...row,
                            typography: "s2",
                            color: "text.tertiary",
                          }}
                        >
                          No personal data found.
                        </Typography>
                      )}
                    </Box>
                  </Section>

                  <Stack direction="row" spacing={1.5}>
                    <Box flex={1}>
                      <Typography sx={head}>
                        What happened — from the trace
                      </Typography>
                      <TextField
                        size="small"
                        fullWidth
                        multiline
                        minRows={3}
                        value={review.happened ?? happenedOf(current, sources)}
                        onChange={(e) => save({ happened: e.target.value })}
                        sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                      />
                    </Box>
                    <Box flex={1}>
                      <Typography sx={head}>
                        What should happen — from the policy, not the
                        agent&apos;s answer
                      </Typography>
                      <TextField
                        size="small"
                        fullWidth
                        multiline
                        minRows={3}
                        value={
                          review.should ?? shouldOf(current, env, envState)
                        }
                        onChange={(e) =>
                          save({ should: e.target.value, validated: null })
                        }
                        sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                      />
                    </Box>
                  </Stack>

                  {/* evidence.md §4: expected outcome, private checks,
                      permitted variations and the destination suite. */}
                  <Section title="Checks, variations and suite">
                    <Box sx={{ ...box, mb: 0.75 }}>
                      {subGoalsOfScenario(current, env, envState).map(
                        (g, i) => (
                          <Typography
                            key={`${i}-${g.text || g}`}
                            sx={{ ...row, typography: "s2" }}
                          >
                            {typeof g === "string" ? g : g.text || g.label}
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary", ml: 0.75 }}
                            >
                              {g.critical === false ? "supporting" : "critical"}{" "}
                              · private
                            </Box>
                          </Typography>
                        ),
                      )}
                    </Box>
                    <TextField
                      size="small"
                      fullWidth
                      label="Allowed variations"
                      placeholder="e.g. Any polite wording; the refund can be offered as credit"
                      value={review.variations}
                      onChange={(e) => save({ variations: e.target.value })}
                      sx={{
                        mb: 0.75,
                        "& .MuiInputBase-input": { typography: "s2" },
                      }}
                    />
                    <Select
                      size="small"
                      displayEmpty
                      value={review.suite}
                      onChange={(e) => save({ suite: e.target.value })}
                      sx={selSx}
                    >
                      <MenuItem value="" sx={{ typography: "s2" }}>
                        No suite — every scenario
                      </MenuItem>
                      {groupsOf(envState).map((g) => (
                        <MenuItem
                          key={g.id}
                          value={g.name}
                          sx={{ typography: "s2" }}
                        >
                          {`Suite: ${g.name}`}
                        </MenuItem>
                      ))}
                      <MenuItem
                        value="Imported regressions"
                        sx={{ typography: "s2" }}
                      >
                        New suite: Imported regressions
                      </MenuItem>
                    </Select>
                  </Section>

                  {existingMatchesOf(current, envState).length > 0 && (
                    <Section title="Looks like an existing scenario">
                      <Box sx={box}>
                        {existingMatchesOf(current, envState).map((m) => (
                          <Stack
                            key={m.id}
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            sx={row}
                          >
                            <Typography
                              sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                              noWrap
                            >
                              {m.title}
                            </Typography>
                            <Button
                              size="small"
                              variant={
                                review.linkTo === m.id
                                  ? "contained"
                                  : "outlined"
                              }
                              color={
                                review.linkTo === m.id ? "primary" : "inherit"
                              }
                              onClick={() =>
                                save({
                                  linkTo: review.linkTo === m.id ? null : m.id,
                                })
                              }
                              sx={{ flexShrink: 0, borderColor: "divider" }}
                            >
                              {review.linkTo === m.id
                                ? "Linked as its next revision"
                                : "Link as its next revision"}
                            </Button>
                          </Stack>
                        ))}
                      </Box>
                    </Section>
                  )}

                  <Section title="Assumptions to review — inferred, not observed">
                    <Box sx={box}>
                      {assumptionsOf(current).map((a) => {
                        /* "Kept blocked" was an older choice — it reads as undecided now. */
                        const v =
                          review.assumptions[a.id] === "blocked"
                            ? null
                            : review.assumptions[a.id];
                        return (
                          <Stack
                            key={a.id}
                            direction="row"
                            spacing={1.25}
                            alignItems="center"
                            sx={row}
                          >
                            <Typography
                              sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                            >
                              {a.line}
                            </Typography>
                            {v ? (
                              <Typography
                                sx={{
                                  typography: "s3",
                                  color: "text.secondary",
                                  flexShrink: 0,
                                }}
                              >
                                {
                                  {
                                    accepted:
                                      "Accepted — stays labelled inferred",
                                    replaced: "Replaced with evidence",
                                  }[v]
                                }
                              </Typography>
                            ) : (
                              <Stack
                                direction="row"
                                spacing={1}
                                sx={{ flexShrink: 0 }}
                              >
                                <ActBtn
                                  onClick={() =>
                                    save({
                                      assumptions: {
                                        ...review.assumptions,
                                        [a.id]: "accepted",
                                      },
                                    })
                                  }
                                >
                                  Accept
                                </ActBtn>
                                <ActBtn
                                  onClick={() =>
                                    save({
                                      assumptions: {
                                        ...review.assumptions,
                                        [a.id]: "replaced",
                                      },
                                    })
                                  }
                                >
                                  Replace with evidence
                                </ActBtn>
                              </Stack>
                            )}
                          </Stack>
                        );
                      })}
                    </Box>
                  </Section>

                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={
                        review.stateMode === "fixture" && !review.fixture
                      }
                      onClick={() => {
                        const res = validateRows(
                          [current],
                          env,
                          currentEnvVersion(env, envState)?.label,
                          envState,
                        )[0]?.result;
                        save({
                          validated: {
                            at: new Date().toISOString(),
                            by: currentUser().name,
                            ok: res?.ok !== false,
                            evidence: STATE_MODES[review.stateMode].evidence,
                          },
                        });
                      }}
                      sx={{ color: "text.primary", borderColor: "divider" }}
                    >
                      Validate
                    </Button>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        flex: 1,
                      }}
                    >
                      {review.validated
                        ? `Validated — ${review.validated.evidence}.`
                        : "Optional: checks it can run in the world and that a correct answer is possible."}
                    </Typography>
                  </Stack>
                </Stack>
              )}
            </Box>
          </Stack>
        )}

        {/* What this add holds, and adding it — a real count, never zero. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
          >
            {allDone || !drafts.length
              ? ""
              : chosen.length
                ? [
                    `${chosen.length} of ${kept.length} drafted ${plural(kept.length, "scenario")} selected`,
                    kept.length > chosen.length
                      ? `the other ${kept.length - chosen.length} keep${kept.length - chosen.length === 1 ? "s" : ""} waiting`
                      : null,
                    removed.size ? `${removed.size} removed` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : kept.length
                  ? "Select at least one drafted scenario to add."
                  : "Every draft is removed. Undo one to add it, or close to confirm."}
          </Typography>
          {allDone || !drafts.length ? (
            <>
              <Button
                size="small"
                variant={allDone && onViewInSuite ? "outlined" : "contained"}
                color={allDone && onViewInSuite ? "inherit" : "primary"}
                onClick={leave}
                sx={
                  allDone && onViewInSuite ? { borderColor: "divider" } : null
                }
              >
                Close
              </Button>
              {allDone && onViewInSuite && (
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={handOff(onViewInSuite)}
                >
                  View in Scenarios
                </Button>
              )}
            </>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!chosen.length}
              onClick={() => {
                onApprove(chosen);
                setAdded((n) => n + chosen.length);
                setSkipped(new Set());
              }}
            >
              {approveLabel
                ? approveLabel(chosen.length)
                : chosen.length
                  ? `Add ${chosen.length} to the suite`
                  : "Add to the suite"}
            </Button>
          )}
        </Stack>
      </Stack>
    </SideDrawer>
  );
}
TraceReviewDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  /* One import; none = every draft that's waiting. */
  importId: PropTypes.string,
  /* (rows) — the drafts the user kept and selected. */
  onApprove: PropTypes.func.isRequired,
  /* (n) => the primary button's label, where adding means something else
     (Error Feed → Fix: the drafts the fix runs on). */
  approveLabel: PropTypes.func,
  /* (row) — focus the environment's builder chat on this draft. */
  onEditWithBuilder: PropTypes.func,
  /* (nudge) — hand the builder what the environment needs to cover. */
  onUpdateEnvironment: PropTypes.func,
  onViewInSuite: PropTypes.func,
};
