import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Radio,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { StatusDot } from "../../components/primitives";
import { clickable, focusRing } from "../../components/clickable";
import { TextLink } from "../../agents/connect/connectBits";
import { validateRows } from "../../_mock/scenarioOrg";
import { currentEnvVersion } from "../../_mock/versions";
import { currentUser } from "../../_mock/scenarioProvenance";
import {
  REDACTION_RULES,
  STATE_MODES,
  assumptionsOf,
  draftSourcesOf,
  draftStatusOf,
  duplicatesOf,
  fixtureOptionsOf,
  happenedOf,
  mappingsOf,
  mergePatch,
  redactionsOf,
  rejectPatch,
  reviewOf,
  reviewPatch,
  shouldOf,
} from "../../_mock/traceReview";

const MONO = "ui-monospace, Menlo, monospace";
const DOT = {
  ready: "passed",
  needs: "needs_input",
  blocked: "failed",
  rejected: "cancelled",
};
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

/**
 * Review an import from production traces (roadmap traces.md): the drafts on
 * the left, each with its status; the open draft's evidence and repairs on
 * the right — starting state, mapping, redaction, what happened vs what
 * should happen, inferred facts, validation. Only ready drafts can be added;
 * rejected ones stay in the import's history with the reason.
 */
export default function TraceReviewDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  importId,
  onApprove,
}) {
  const drafts = useMemo(
    () =>
      (envState?.scenarios || []).filter(
        (r) => r.proposed && r.traceImport?.id === importId,
      ),
    [envState?.scenarios, importId],
  );
  const [openId, setOpenId] = useState(null);
  const [rejecting, setRejecting] = useState("");
  const current = drafts.find((d) => d.id === openId) || drafts[0] || null;
  const info = (drafts[0] || current)?.traceImport || {};
  const statusOf = (d) => {
    const src = draftSourcesOf(d, env, envState);
    return draftStatusOf(d, reviewOf(d, src), mappingsOf(d, env, src));
  };
  const statuses = drafts.map((d) => ({ d, s: statusOf(d) }));
  const ready = statuses.filter((x) => x.s.id === "ready").map((x) => x.d);
  const blocked = statuses.filter((x) => x.s.id === "blocked").length;
  const dupes = duplicatesOf(drafts);
  const rejected = (envState?.traceImports || []).filter(
    (x) => x.importId === importId && x.rejected,
  );
  const save = (fields) =>
    current && patch(reviewPatch(envState, current.id, fields));

  if (!open) return <SideDrawer open={false} onClose={onClose} />;
  const sources = current ? draftSourcesOf(current, env, envState) : [];
  const review = current ? reviewOf(current, sources) : null;
  const maps = current ? mappingsOf(current, env, sources) : [];
  const status = current ? statusOf(current) : null;
  const missingState = sources.some((s) => !s.evidence.startingState);
  const hasTools = sources.every((s) => s.evidence.tools);

  return (
    <SideDrawer open onClose={onClose} width={960}>
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
              Review drafts from production traces
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {`${(info.ids || []).join(", ")} · project ${info.project || "—"} · ${info.range || ""} · goal: ${info.goal || "—"}`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
          {/* The drafts, each with its status. */}
          <Box
            sx={{
              width: 300,
              flexShrink: 0,
              borderRight: "1px solid",
              borderColor: "divider",
              overflow: "auto",
            }}
          >
            {statuses.map(({ d, s }) => (
              <Box
                key={d.id}
                {...clickable(() => setOpenId(d.id))}
                sx={{
                  ...focusRing,
                  px: 1.5,
                  py: 1,
                  cursor: "pointer",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  bgcolor:
                    current?.id === d.id ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                  {d.title}
                </Typography>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <StatusDot status={DOT[s.id]} size={5} live={false} />
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {`${s.label}${s.why ? ` · ${s.why}` : ""}`}
                  </Typography>
                </Stack>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontFamily: MONO,
                  }}
                >
                  {(d.traceImport?.sourceIds || []).join(", ")}
                  {d.generatedVariant ? " · generated variant" : ""}
                </Typography>
              </Box>
            ))}
            {dupes.length > 0 && (
              <Box sx={{ px: 1.5, py: 1 }}>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${dupes.length} set${dupes.length === 1 ? "" : "s"} of drafts look like the same situation.`}
                </Typography>
                {dupes.map((ids) => (
                  <TextLink
                    key={ids.join()}
                    onClick={() => patch(mergePatch(envState, ids))}
                  >
                    {`Merge ${ids.length} — every source is kept`}
                  </TextLink>
                ))}
              </Box>
            )}
            {!drafts.length && (
              <Typography
                sx={{
                  px: 1.5,
                  py: 1.5,
                  typography: "s2",
                  color: "text.tertiary",
                }}
              >
                Nothing left to review in this import.
              </Typography>
            )}
          </Box>

          {/* The open draft's review. */}
          <Box sx={{ flex: 1, minWidth: 0, overflow: "auto", p: 2 }}>
            {current && (
              <Stack spacing={2}>
                <Box>
                  <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                    {current.title}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {current.generatedVariant
                      ? `Generated variant of ${current.generatedVariant.parentTrace} — not something that happened in production.`
                      : `From ${(current.traceImport?.sourceIds || []).join(", ")}${current.traceImport?.merged?.length ? ` · merged with ${current.traceImport.merged.length} more` : ""}`}
                  </Typography>
                </Box>

                <Section title="Evidence">
                  <Box sx={box}>
                    {sources.map((s) => (
                      <Typography
                        key={s.traceId}
                        sx={{ ...row, typography: "s2" }}
                      >
                        <Box component="span" sx={{ fontFamily: MONO, mr: 1 }}>
                          {s.traceId}
                        </Box>
                        {`agent ${s.agentVersion} · ${s.outcome === "failed" ? "failed" : "passed"} · transcript${s.evidence.tools ? " · tool calls" : ""}${s.evidence.startingState ? " · starting state" : ""}`}
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
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
                  >
                    {`Evidence class: ${STATE_MODES[review.stateMode].evidence}.`}
                  </Typography>
                </Section>

                {missingState && (
                  <Section title="Starting state — the trace didn't record it">
                    <Stack spacing={0.5}>
                      {["fixture", "replay", "synthetic", "blocked"].map(
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
                            <TextLink
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
                            </TextLink>
                            <TextLink
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
                            </TextLink>
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
                      What should happen — from the policy, not the agent&apos;s
                      answer
                    </Typography>
                    <TextField
                      size="small"
                      fullWidth
                      multiline
                      minRows={3}
                      value={review.should ?? shouldOf(current, env, envState)}
                      onChange={(e) =>
                        save({ should: e.target.value, validated: null })
                      }
                      sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                    />
                  </Box>
                </Stack>

                <Section title="Inferred, not observed">
                  <Box sx={box}>
                    {assumptionsOf(current).map((a) => {
                      const v = review.assumptions[a.id];
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
                                color:
                                  v === "blocked"
                                    ? "status.warning.fg"
                                    : "text.secondary",
                                flexShrink: 0,
                              }}
                            >
                              {
                                {
                                  accepted:
                                    "Accepted — stays labelled inferred",
                                  replaced: "Replaced with evidence",
                                  blocked: "Kept blocked",
                                }[v]
                              }
                            </Typography>
                          ) : (
                            <Stack
                              direction="row"
                              spacing={1}
                              sx={{ flexShrink: 0 }}
                            >
                              <TextLink
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
                              </TextLink>
                              <TextLink
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
                              </TextLink>
                              <TextLink
                                onClick={() =>
                                  save({
                                    assumptions: {
                                      ...review.assumptions,
                                      [a.id]: "blocked",
                                    },
                                  })
                                }
                              >
                                Keep blocked
                              </TextLink>
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
                      review.stateMode === "blocked" ||
                      (review.stateMode === "fixture" && !review.fixture)
                    }
                    onClick={() => {
                      const res = validateRows(
                        [current],
                        env,
                        currentEnvVersion(env, envState)?.label,
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
                    sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
                  >
                    {review.validated
                      ? `Validated — ${review.validated.evidence}.`
                      : status.id === "blocked"
                        ? `Blocked — ${status.why}.`
                        : "Checks it can run in the world and that a correct answer is possible."}
                  </Typography>
                </Stack>

                {/* Reject keeps it in the import's history with the reason. */}
                <Stack direction="row" spacing={1} alignItems="center">
                  <TextField
                    size="small"
                    placeholder="Why reject it (kept in the import's history)"
                    value={rejecting}
                    onChange={(e) => setRejecting(e.target.value)}
                    sx={{
                      flex: 1,
                      "& .MuiInputBase-input": { typography: "s2" },
                    }}
                  />
                  <Button
                    size="small"
                    disabled={!rejecting.trim()}
                    onClick={() => {
                      patch(rejectPatch(envState, current, rejecting.trim()));
                      setRejecting("");
                      setOpenId(null);
                    }}
                    sx={{ color: "text.secondary" }}
                  >
                    Reject draft
                  </Button>
                </Stack>
              </Stack>
            )}
          </Box>
        </Stack>

        {/* The import's counts, and adding what's ready. */}
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
            {[
              `${drafts.length} draft${drafts.length === 1 ? "" : "s"} from ${(info.ids || []).length} trace${(info.ids || []).length === 1 ? "" : "s"}`,
              `${ready.length} ready`,
              blocked ? `${blocked} blocked` : null,
              dupes.length
                ? `${dupes.reduce((a, ids) => a + ids.length - 1, 0)} possible duplicate${dupes.length === 1 ? "" : "s"}`
                : null,
              rejected.length ? `${rejected.length} rejected` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!ready.length}
            onClick={() => onApprove(ready)}
          >
            {`Add ${ready.length} ready to the environment`}
          </Button>
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
  importId: PropTypes.string,
  onApprove: PropTypes.func.isRequired,
};
