import PropTypes from "prop-types";
import { useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CopyCommandButton from "../../components/CopyCommandButton";
import { useEnvState } from "../../store";
import { useRoleGates } from "../../_mock/previewRole";
import { currentEnvVersion } from "../../_mock/versions";
import {
  SUPPORTS,
  SUGGESTED_TAGS,
  tagsOf,
  groupsOf,
  groupsOfRow,
  tagPatch,
  groupPatch,
  groundPatch,
  ungroundPatch,
  staleGroundingOf,
  keepPinnedGroundPatch,
  regroundPatch,
  duplicatePatch,
  validateRows,
  validatePatch,
  promotePatch,
  scenarioCli,
} from "../../_mock/scenarioOrg";

const HEAD = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  mb: 0.75,
};
const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

/* The evidence class a production-derived case runs under, when its starting state wasn't recorded. */
const EVIDENCE = [
  {
    id: "fixture",
    label: "Add fixture",
    line: "You supply the missing records — an exact reconstruction.",
  },
  {
    id: "replay",
    label: "Recorded replay",
    line: "Tool answers are replayed as recorded — tests the response, not the state.",
  },
  {
    id: "synthetic",
    label: "Labeled synthetic reproduction",
    line: "Generated starting state, labelled as such on every result.",
  },
];

/**
 * Organise and ground one scenario (roadmap scenarios.md): its tags and
 * groups, the evidence its expected behaviour rests on (and what to do when
 * that source moves on), what happened versus what should happen for a
 * production case, and the lifecycle actions — duplicate to edit, validate,
 * promote. Lives in the scenario's drawer.
 */
export default function ScenarioOrgPart({ env, row, locked = false }) {
  const { envState, patch } = useEnvState(env.id);
  const gates = useRoleGates();
  const live = (envState.scenarios || []).find((r) => r.id === row.id) || row;
  const ro = locked || !gates.canEdit;
  const [tagInput, setTagInput] = useState("");
  const [groupInput, setGroupInput] = useState("");
  const [citing, setCiting] = useState(null);
  const [validation, setValidation] = useState(null);
  const tags = tagsOf(envState);
  const myTags = tags.filter((t) => (live.tagIds || []).includes(t.id));
  const myGroups = groupsOfRow(envState, live.id);
  const sources = (envState.sources || []).filter(
    (s) => s.inEnv && !s.archived && s.visibility !== "evaluator",
  );
  const stale = staleGroundingOf(live, envState);
  const fromProduction = ["production", "error-feed"].includes(
    live.provenance?.source || live.source,
  );
  const grounded = (live.grounding || []).some(
    (g) => g.supports === "expected" || g.supports === "policy",
  );

  const addCite = () => {
    const src = sources.find((s) => s.id === citing.sourceId);
    const passage = src?.parsed?.passages?.find(
      (p) => p.id === citing.passageId,
    );
    patch(
      groundPatch(envState, live.id, {
        sourceId: src?.id || null,
        ref: src
          ? `${src.name}${passage ? ` · p.${passage.page}` : ""}`
          : citing.ref,
        excerpt: passage?.text || citing.excerpt,
        supports: citing.supports,
        version: src?.version || null,
        note: citing.note || "",
      }),
    );
    setCiting(null);
  };

  return (
    <Stack spacing={2}>
      {/* tags and groups */}
      <Box>
        <Typography sx={HEAD}>Tags and groups</Typography>
        <Box sx={{ ...box, p: 1.25 }}>
          <Stack
            direction="row"
            spacing={0.75}
            sx={{ flexWrap: "wrap", rowGap: 0.75, alignItems: "center" }}
          >
            {myTags.map((t) => (
              <Chip
                key={t.id}
                size="small"
                label={t.name}
                onDelete={
                  ro
                    ? undefined
                    : () => patch(tagPatch(envState, [live.id], t.name, false))
                }
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "transparent",
                  "&::before": {
                    content: '""',
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: t.color,
                    ml: 1,
                  },
                }}
              />
            ))}
            {!ro && (
              <Autocomplete
                freeSolo
                size="small"
                options={[
                  ...new Set([...tags.map((t) => t.name), ...SUGGESTED_TAGS]),
                ].filter((n) => !myTags.some((t) => t.name === n))}
                inputValue={tagInput}
                onInputChange={(_, v) => setTagInput(v)}
                onChange={(_, v) => {
                  if (v) patch(tagPatch(envState, [live.id], v, true));
                  setTagInput("");
                }}
                renderInput={(p) => (
                  <TextField {...p} placeholder="Add tag — risk=duplicate" />
                )}
                sx={{ width: 220 }}
              />
            )}
          </Stack>
          <Stack
            direction="row"
            spacing={0.75}
            sx={{ flexWrap: "wrap", rowGap: 0.75, alignItems: "center", mt: 1 }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              In groups:
            </Typography>
            {myGroups.length === 0 && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                none
              </Typography>
            )}
            {myGroups.map((g) => (
              <Chip
                key={g.id}
                size="small"
                icon={<Iconify icon="solar:folder-2-linear" width={12} />}
                label={g.name}
                onDelete={
                  ro
                    ? undefined
                    : () =>
                        patch(groupPatch(envState, [live.id], g.name, false))
                }
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "transparent",
                }}
              />
            ))}
            {!ro && (
              <Autocomplete
                freeSolo
                size="small"
                options={groupsOf(envState)
                  .map((g) => g.name)
                  .filter((n) => !myGroups.some((g) => g.name === n))}
                inputValue={groupInput}
                onInputChange={(_, v) => setGroupInput(v)}
                onChange={(_, v) => {
                  if (v) patch(groupPatch(envState, [live.id], v, true));
                  setGroupInput("");
                }}
                renderInput={(p) => (
                  <TextField
                    {...p}
                    placeholder="Add to group — Refund release gate"
                  />
                )}
                sx={{ width: 240 }}
              />
            )}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}>
            A tag describes and filters; a group selects. Being in two groups is
            still one scenario, with one history.
          </Typography>
        </Box>
      </Box>

      {/* grounding */}
      <Box>
        <Typography
          sx={HEAD}
        >{`Evidence and grounding${grounded ? "" : " · not grounded yet"}`}</Typography>
        <Box sx={box}>
          {(live.grounding || []).length === 0 && (
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                px: 1.5,
                py: 1.25,
              }}
            >
              Nothing yet says why the expected behaviour is right. Cite the
              policy passage, the record or the expert note it rests on.
            </Typography>
          )}
          {(live.grounding || []).map((g) => {
            const moved = stale.find((x) => x.id === g.id);
            return (
              <Box
                key={g.id}
                sx={{
                  px: 1.5,
                  py: 1,
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      width: 104,
                      flexShrink: 0,
                    }}
                  >
                    {SUPPORTS.find((x) => x.id === g.supports)?.label}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
                  >{`${g.ref}${g.version ? ` · v${g.version}` : ""}${g.pinned ? " · pinned" : ""}`}</Typography>
                  {!ro && (
                    <Button
                      size="small"
                      onClick={() =>
                        patch(ungroundPatch(envState, live.id, g.id))
                      }
                      sx={{ minWidth: 0, color: "text.tertiary" }}
                    >
                      Remove
                    </Button>
                  )}
                </Stack>
                {g.excerpt && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", pl: 13.5 }}
                  >{`“${g.excerpt}”`}</Typography>
                )}
                {moved && !g.pinned && (
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ pl: 13.5, mt: 0.75 }}
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "status.warning.fg",
                        flex: 1,
                      }}
                    >
                      {`${moved.sourceName} is now v${moved.now} — this claim still rests on v${g.version}.`}
                    </Typography>
                    {!ro && (
                      <>
                        <Button
                          size="small"
                          onClick={() =>
                            patch(
                              keepPinnedGroundPatch(envState, live.id, g.id),
                            )
                          }
                          sx={{ color: "text.secondary" }}
                        >
                          Keep pinned
                        </Button>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() =>
                            patch(regroundPatch(envState, live.id, g.id))
                          }
                          sx={{ color: "text.primary", borderColor: "divider" }}
                        >
                          Re-ground
                        </Button>
                      </>
                    )}
                  </Stack>
                )}
              </Box>
            );
          })}
          {!ro && !citing && (
            <Box
              sx={{
                px: 1.5,
                py: 1,
                borderTop: (live.grounding || []).length ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              <Button
                size="small"
                startIcon={
                  <Iconify icon="solar:link-minimalistic-linear" width={14} />
                }
                onClick={() =>
                  setCiting({
                    sourceId: sources[0]?.id || "",
                    passageId: "",
                    supports: "expected",
                    ref: "",
                    excerpt: "",
                  })
                }
                sx={{ color: "text.secondary" }}
              >
                Cite evidence
              </Button>
            </Box>
          )}
          {citing && (
            <Stack
              spacing={1}
              sx={{ p: 1.5, borderTop: "1px solid", borderColor: "divider" }}
            >
              <Stack direction="row" spacing={1}>
                <Select
                  size="small"
                  value={citing.supports}
                  onChange={(e) =>
                    setCiting({ ...citing, supports: e.target.value })
                  }
                  sx={{ width: 170, typography: "s2" }}
                >
                  {SUPPORTS.map((x) => (
                    <MenuItem
                      key={x.id}
                      value={x.id}
                      sx={{ typography: "s2" }}
                    >{`Supports: ${x.label}`}</MenuItem>
                  ))}
                </Select>
                {sources.length > 0 && (
                  <Select
                    size="small"
                    displayEmpty
                    value={citing.sourceId}
                    onChange={(e) =>
                      setCiting({
                        ...citing,
                        sourceId: e.target.value,
                        passageId: "",
                      })
                    }
                    sx={{ flex: 1, typography: "s2" }}
                  >
                    <MenuItem value="" sx={{ typography: "s2" }}>
                      Type a reference instead
                    </MenuItem>
                    {sources.map((s) => (
                      <MenuItem
                        key={s.id}
                        value={s.id}
                        sx={{ typography: "s2" }}
                      >{`${s.name} · v${s.version}`}</MenuItem>
                    ))}
                  </Select>
                )}
              </Stack>
              {citing.sourceId ? (
                <Select
                  size="small"
                  displayEmpty
                  value={citing.passageId}
                  onChange={(e) =>
                    setCiting({ ...citing, passageId: e.target.value })
                  }
                  sx={{ typography: "s2" }}
                >
                  <MenuItem value="" sx={{ typography: "s2" }}>
                    The whole source
                  </MenuItem>
                  {(
                    sources.find((s) => s.id === citing.sourceId)?.parsed
                      ?.passages || []
                  ).map((p) => (
                    <MenuItem
                      key={p.id}
                      value={p.id}
                      sx={{ typography: "s2", whiteSpace: "normal" }}
                    >{`p.${p.page} — ${p.text}`}</MenuItem>
                  ))}
                </Select>
              ) : (
                <>
                  <TextField
                    size="small"
                    label="Reference"
                    placeholder="Refund policy v2 · §3, or expert note from Maya"
                    value={citing.ref}
                    onChange={(e) =>
                      setCiting({ ...citing, ref: e.target.value })
                    }
                  />
                  <TextField
                    size="small"
                    label="Excerpt"
                    value={citing.excerpt}
                    onChange={(e) =>
                      setCiting({ ...citing, excerpt: e.target.value })
                    }
                    multiline
                  />
                </>
              )}
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button
                  size="small"
                  onClick={() => setCiting(null)}
                  sx={{ color: "text.secondary" }}
                >
                  Cancel
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!citing.sourceId && !citing.ref.trim()}
                  onClick={addCite}
                >
                  Cite
                </Button>
              </Stack>
            </Stack>
          )}
        </Box>
      </Box>

      {/* production cases: observed vs expected, and the evidence class */}
      {fromProduction && (
        <Box>
          <Typography sx={HEAD}>
            What happened and what should happen
          </Typography>
          <Stack spacing={1} sx={{ ...box, p: 1.25 }}>
            <TextField
              size="small"
              label="What happened (observed)"
              multiline
              value={live.observed ?? live.productionQuote ?? ""}
              disabled={ro}
              onChange={(e) =>
                patch({
                  scenarios: envState.scenarios.map((r) =>
                    r.id === live.id ? { ...r, observed: e.target.value } : r,
                  ),
                })
              }
              helperText="What the agent did in production — not the gold answer."
            />
            <TextField
              size="small"
              label="What should happen"
              multiline
              value={live.expected || ""}
              disabled={ro}
              onChange={(e) =>
                patch({
                  scenarios: envState.scenarios.map((r) =>
                    r.id === live.id
                      ? {
                          ...r,
                          expected: e.target.value,
                          outcome: e.target.value,
                        }
                      : r,
                  ),
                })
              }
              helperText="From the policy or a reviewer — cite it above."
            />
            <Box>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
              >
                {live.evidenceClass
                  ? "Starting state"
                  : "Starting state wasn't recorded — exact replay is blocked until you choose"}
              </Typography>
              <Stack spacing={0.5}>
                {EVIDENCE.map((x) => (
                  <Stack
                    key={x.id}
                    direction="row"
                    spacing={1}
                    role="radio"
                    aria-checked={live.evidenceClass === x.id}
                    onClick={() =>
                      !ro &&
                      patch({
                        scenarios: envState.scenarios.map((r) =>
                          r.id === live.id ? { ...r, evidenceClass: x.id } : r,
                        ),
                      })
                    }
                    sx={{
                      cursor: ro ? "default" : "pointer",
                      alignItems: "flex-start",
                    }}
                  >
                    <Iconify
                      icon={
                        live.evidenceClass === x.id
                          ? "solar:check-circle-bold"
                          : "solar:circle-linear"
                      }
                      width={14}
                      sx={{ mt: "3px", color: "text.secondary" }}
                    />
                    <Typography sx={{ typography: "s2" }}>
                      {x.label}
                      <Box
                        component="span"
                        sx={{ color: "text.tertiary" }}
                      >{` — ${x.line}`}</Box>
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          </Stack>
        </Box>
      )}

      {/* lifecycle */}
      <Box>
        <Typography sx={HEAD}>
          {live.promoted
            ? `Promoted to ${live.promoted.kind} — its checks are frozen`
            : live.parentId
              ? "Duplicated from another scenario"
              : "Lifecycle"}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1 }}>
          <Button
            size="small"
            variant="outlined"
            disabled={ro}
            onClick={() => {
              const { copies, ...p } = duplicatePatch(envState, [live]);
              patch(p);
              enqueueSnackbar(
                `Duplicated as a draft — ${copies[0].title}. The original keeps its identity and history.`,
                { variant: "success" },
              );
            }}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Duplicate to edit
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={ro}
            onClick={() => {
              const res = validateRows(
                [live],
                env,
                currentEnvVersion(env, envState).label,
              );
              patch(validatePatch(envState, res));
              setValidation(res[0].result);
            }}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Validate
          </Button>
          {!live.promoted && (
            <>
              <Button
                size="small"
                variant="outlined"
                disabled={ro}
                onClick={() =>
                  patch(promotePatch(envState, [live.id], "regression"))
                }
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Promote to regression
              </Button>
              <Button
                size="small"
                disabled={ro}
                onClick={() =>
                  patch(promotePatch(envState, [live.id], "benchmark"))
                }
                sx={{ color: "text.secondary" }}
              >
                Promote to benchmark
              </Button>
            </>
          )}
          <CopyCommandButton command={scenarioCli.validate([live.id])} />
        </Stack>
        {validation && (
          <Box sx={{ ...box, mt: 1, p: 1.25 }}>
            {validation.gates.map((g) => (
              <Stack key={g.id} direction="row" spacing={1} alignItems="center">
                <Iconify
                  icon={
                    g.pass
                      ? "solar:check-circle-linear"
                      : "solar:close-circle-linear"
                  }
                  width={14}
                  sx={{ color: g.pass ? "text.secondary" : "status.danger.fg" }}
                />
                <Typography sx={{ typography: "s2" }}>{g.label}</Typography>
              </Stack>
            ))}
            {!validation.pass && (
              <Typography
                sx={{ typography: "s3", color: "status.danger.fg", mt: 0.5 }}
              >{`Blocked — ${validation.failed.reason}`}</Typography>
            )}
          </Box>
        )}
      </Box>
    </Stack>
  );
}

ScenarioOrgPart.propTypes = {
  env: PropTypes.object.isRequired,
  row: PropTypes.object.isRequired,
  locked: PropTypes.bool,
};
