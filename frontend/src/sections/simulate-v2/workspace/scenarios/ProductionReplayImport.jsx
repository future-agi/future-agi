import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Collapse,
  IconButton,
  TextField,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../../components/primitives";
import { replayScenarioOf, asProposal } from "../../_mock/scenarioSources";
import {
  QUEUE_FROM,
  TRACE_GOALS,
  draftingCostOf,
  fromTracesCli,
  productionTracesOf,
  rangeOf,
  traceDraftsOf,
  traceProjectOf,
} from "../../_mock/traceImport";
import {
  observeSelectionOf,
  selectionNoun,
  selectionTracesOf,
} from "../../_mock/observeSelection";
import { ensureTag, groupPatch } from "../../_mock/scenarioOrg";
import {
  EVIDENCE_KINDS,
  parseDataset,
  parseTranscript,
} from "../../_mock/evidenceSources";
import { useConnectedAgents, useEnvState, useSimStore } from "../../store";
import { envAgentKey } from "../../_mock/agentRegistry";
import { observeProjectOf } from "../../agents/observeProject";
import { observeEnvsOf } from "../../_mock/runningNow";
import CopyCommandButton from "../../components/CopyCommandButton";
import { TextLink } from "../../agents/connect/connectBits";
import { relativeTime } from "../../_mock/scenarioProvenance";
import { PersonalDataTag } from "./ScenarioStateBits";
import { FAKE_CLOSE, FAKE_OPEN } from "../../_mock/rsiModel";
import { clickable, focusRing } from "../../components/clickable";

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};
const MONO = "ui-monospace, Menlo, monospace";
const fmtDuration = (s) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Production traces to scenarios (roadmap traces.md, Week 2), in two steps.
 * Pick: exact traces from the agent's Observe project — each with its trace
 * ID, agent version, outcome and the evidence it lacks, as columns; "select all
 * matching" is its own counted choice. Freeze: the picked IDs are fixed (new
 * traces or a changed filter don't change the import), then what the new
 * scenarios should represent and where they go. Drafts then wait for review.
 */
export default function ProductionReplayImport({
  mode = "observe",
  env,
  envState,
  onAdd,
  selected,
}) {
  /* Two of Add scenarios' sources. "observe": From production — the agent's
     Observe traces, or exactly what Observe's "Create environment test"
     handed over (?from=observe). "files": an uploaded transcript or a
     CSV/JSON file. An issue's traces come through From an Error Feed issue,
     and failed simulations through From failing cases. */
  const [searchParams] = useSearchParams();
  const kinds = mode === "files" ? ["transcript", "dataset"] : ["observe"];
  const [src, setSrc] = useState(kinds[0]);
  const fileRef = useRef(null);
  const navigate = useNavigate();
  /* The exact sessions, traces or calls picked in Observe — they open frozen,
     so nothing is picked twice from another list (evidence.md, step 1). */
  const sel = useMemo(
    () => (mode === "observe" ? observeSelectionOf(searchParams) : null),
    [mode, searchParams],
  );
  const selTraces = useMemo(
    () => selectionTracesOf(sel, productionTracesOf(env, envState)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sel, env?.id],
  );
  const traces = useMemo(() => {
    if (src === "observe")
      return sel ? selTraces : productionTracesOf(env, envState);
    return (envState?.evidenceUploads || []).filter(
      (u) => u.sourceKind === src,
    );
  }, [src, env, envState, sel, selTraces]);
  const noun = EVIDENCE_KINDS[src].noun;
  /* The Observe project this environment takes traces from — the same
     link Observe's Create environment test reads. */
  const { state: simState } = useSimStore();
  const observeProjectId = useMemo(
    () =>
      observeEnvsOf(simState).find((x) => x.envId === env?.id)?.projectId ||
      null,
    [simState, env?.id],
  );
  /* The tested agent's tracing project, as linked now — a project linked
     after the environment was built counts too. */
  const { agents: connected } = useConnectedAgents();
  const agentProject = useMemo(() => {
    const a = envState?.agent;
    if (!a) return null;
    const rec = connected.find((x) => x.key === envAgentKey(a, env?.id));
    return rec
      ? observeProjectOf(rec, simState, connected)?.name || null
      : null;
  }, [envState?.agent, connected, simState, env?.id]);
  const project = sel?.project || agentProject || traceProjectOf(env, envState);
  const unit = sel?.kind || "traces";
  /* What each becomes as a replay, already scrubbed — the only form shown. */
  const drafts = useMemo(
    () => new Map(traces.map((c) => [c.id, replayScenarioOf(c, env)])),
    [traces, env],
  );
  const alreadyIn = useMemo(
    () => new Set((selected || []).map((s) => s.id)),
    [selected],
  );
  const [picked, setPicked] = useState({});
  const [step, setStep] = useState(sel ? "freeze" : "pick");
  const [frozen, setFrozen] = useState(() => selTraces.map((t) => t.id));
  /* What the new scenarios are — one or more kinds at once (Chintan,
     25 Nov); the first is the lead for the command line and old drafts. */
  const [goals, setGoals] = useState(["reproduce"]);
  const goal = goals[0] || "reproduce";
  const setGoal = (g) => setGoals([g]);
  const toggleGoal = (g) =>
    setGoals((list) =>
      list.includes(g)
        ? list.length > 1
          ? list.filter((x) => x !== g)
          : list
        : [...list, g],
    );
  const [group, setGroup] = useState("");
  const [tags, setTags] = useState("");
  const { patch } = useEnvState(env?.id);
  const onUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    const read = await Promise.all(
      files.map((f) => f.text().then((t) => [f.name, t])),
    );
    const items = read.flatMap(([name, text], i) =>
      src === "transcript"
        ? [
            parseTranscript(
              text,
              name,
              (envState?.evidenceUploads || []).length + i + 1,
            ),
          ].filter(Boolean)
        : parseDataset(text, name),
    );
    if (items.length)
      patch({
        evidenceUploads: [...(envState?.evidenceUploads || []), ...items],
      });
  };
  /* A large import's queue: { importId, rows, done, running }. */
  const [queue, setQueue] = useState(null);
  useEffect(() => {
    if (!queue?.running) return undefined;
    if (queue.done >= queue.rows.length) {
      setQueue((q) => ({ ...q, running: false }));
      return undefined;
    }
    const t = setTimeout(
      () => setQueue((q) => (q?.running ? { ...q, done: q.done + 1 } : q)),
      450,
    );
    return () => clearTimeout(t);
  }, [queue]);
  /* Draft imports: a frozen selection saved to finish later. */
  const allDrafts = envState?.traceImportDrafts || [];
  /* Each source lists only its own saved drafts (older ones are Observe's). */
  const drafts0 = allDrafts.filter((d) => kinds.includes(d.src || "observe"));
  const [draftId, setDraftId] = useState(null);
  const saveDraft = () => {
    const id = draftId || `dimp_${Date.now().toString(36)}`;
    patch({
      traceImportDrafts: [
        ...allDrafts.filter((d) => d.id !== id),
        {
          id,
          src,
          at: new Date().toISOString(),
          frozen,
          traceIds: frozenTraces.map((t) => t.traceId),
          goal,
          goals,
          group,
          tags,
        },
      ],
    });
    setDraftId(null);
    setPicked({});
    setStep("pick");
  };
  const resumeDraft = (d) => {
    setDraftId(d.id);
    if (d.src && kinds.includes(d.src)) setSrc(d.src);
    setFrozen(d.frozen);
    setGoals(d.goals || [d.goal]);
    setGroup(d.group || "");
    setTags(d.tags || "");
    setStep("freeze");
  };
  const dropDraft = (id = draftId) => {
    if (!id) return;
    patch({ traceImportDrafts: allDrafts.filter((d) => d.id !== id) });
    setDraftId(null);
  };
  const chosen = traces.filter((c) => picked[c.id]);
  const frozenTraces = traces.filter((t) => frozen.includes(t.id));
  const goalMeta = TRACE_GOALS.find((g) => g.id === goal);
  const noState = frozenTraces.filter((t) => !t.evidence.startingState);
  const passedTraces = frozenTraces.filter((t) => t.outcome === "passed");
  const noTools = frozenTraces.filter((t) => !t.evidence.tools);
  /* Every chosen kind's drafts, one copy each (a passed trace under
     Reproduce and under Keep a success is the same regression). */
  const draftsFor = (importInfo) => {
    const seen = new Set();
    return goals
      .flatMap((g) => traceDraftsOf(frozenTraces, g, env, importInfo))
      .filter((r) => (seen.has(r.id) ? false : seen.add(r.id)));
  };

  const create = () => {
    const importInfo = {
      sourceKind: src === "observe" && unit === "calls" ? "simCall" : src,
      unit: src === "observe" ? unit : undefined,
      id: `imp_${Date.now().toString(36)}`,
      at: new Date().toISOString(),
      project,
      range: rangeOf(frozenTraces),
      ids: frozenTraces.map((t) => t.traceId),
      /* What Observe handed over travels with the drafts, so each one's
         review reads the evidence of exactly what was picked. */
      ...(sel && {
        sources: frozenTraces.map(
          ({
            id,
            traceId,
            outcome,
            at,
            evidence,
            agentVersion,
            spans,
            unit: u,
          }) => ({
            id,
            traceId,
            outcome,
            at,
            evidence,
            agentVersion,
            spans,
            unit: u,
          }),
        ),
      }),
    };
    /* A dataset row's expected behaviour is the draft's claim to review. */
    const expectedOf = new Map(
      frozenTraces
        .filter((t) => t.expected)
        .map((t) => [t.traceId, t.expected]),
    );
    const rows = draftsFor(importInfo)
      .filter((r) => !alreadyIn.has(r.id))
      .map((r) => {
        const e = expectedOf.get(r.traceImport?.sourceIds?.[0]);
        return e ? { ...r, expected: e } : r;
      });
    /* A large selection drafts as a queue — progress, cost, cancel. */
    if (frozenTraces.length >= QUEUE_FROM)
      return setQueue({
        importId: importInfo.id,
        rows,
        done: 0,
        running: true,
      });
    return finish(rows);
  };
  /* Add the drafted rows, with the chosen tags and group. */
  const finish = (rows) => {
    dropDraft();
    /* Tags and the group are organisation: set now, kept when the drafts are approved. */
    const names = tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    let es = envState;
    const tagIds = [];
    names.forEach((n) => {
      const { tag, tags: all } = ensureTag(es, n);
      es = { ...es, scenarioTags: all };
      tagIds.push(tag.id);
    });
    const org = {
      ...(names.length && { scenarioTags: es.scenarioTags }),
      ...(group.trim() &&
        groupPatch(
          envState,
          rows.map((r) => r.id),
          group.trim(),
        )),
    };
    if (Object.keys(org).length) patch(org);
    onAdd(
      rows.map((r) =>
        asProposal(
          tagIds.length ? { ...r, tagIds } : r,
          src === "observe"
            ? unit === "calls"
              ? "simulation"
              : "production"
            : {
                transcript: "transcript",
                dataset: "dataset-import",
              }[src],
        ),
      ),
    );
  };

  if (queue) {
    const total = queue.rows.length;
    const cost = draftingCostOf(total);
    const finished = queue.done >= total;
    return (
      <SectionCard
        sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
      >
        <Box
          sx={{
            px: 2.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {finished
              ? `${total} drafts ready`
              : queue.running
                ? `Drafting ${queue.done + 1} of ${total}…`
                : `Cancelled — ${queue.done} of ${total} drafted`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Import ${queue.importId} · ≈ ${cost.low}–${cost.high} credits for drafting · completed drafts are kept if you cancel`}
          </Typography>
        </Box>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {queue.rows.map((r, i) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2.5,
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                {r.title}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: i < queue.done ? "text.secondary" : "text.tertiary",
                  flexShrink: 0,
                }}
              >
                {i < queue.done
                  ? "Drafted"
                  : i === queue.done && queue.running
                    ? "Drafting…"
                    : queue.running
                      ? "Queued"
                      : "Not drafted"}
              </Typography>
            </Stack>
          ))}
        </Box>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {queue.running && (
            <Button
              size="small"
              onClick={() => setQueue((q) => ({ ...q, running: false }))}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
          )}
          {!queue.running && !finished && (
            <Button
              variant="outlined"
              size="small"
              onClick={() => setQueue((q) => ({ ...q, running: true }))}
              sx={{ color: "text.primary" }}
            >
              {`Retry the ${total - queue.done} left`}
            </Button>
          )}
          {!queue.running && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!queue.done}
              onClick={() => finish(queue.rows.slice(0, queue.done))}
            >
              {`Add the ${queue.done} drafted for review`}
            </Button>
          )}
        </Stack>
      </SectionCard>
    );
  }

  if (step === "freeze")
    return (
      <SectionCard
        sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
      >
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <Stack spacing={2} sx={{ px: 2.5, py: 2 }}>
            {/* The exact selection, frozen. */}
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                px: 1.5,
                py: 1,
              }}
            >
              <Stack direction="row" alignItems="baseline" spacing={1}>
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  {sel
                    ? `${frozenTraces.length} ${selectionNoun(unit, frozenTraces.length)} from Observe`
                    : `${frozenTraces.length} trace${frozenTraces.length === 1 ? "" : "s"} frozen`}
                </Typography>
                {/* Picked in Observe: the selection is changed there. */}
                <TextLink
                  onClick={() => (sel ? navigate(-1) : setStep("pick"))}
                >
                  {sel ? "Change in Observe" : "Change selection"}
                </TextLink>
              </Stack>
              <Typography sx={{ typography: "s2", fontFamily: MONO, mt: 0.25 }}>
                {frozenTraces.map((t) => t.traceId).join(", ")}
              </Typography>
              {/* 25 Nov: the frozen-selection note is one short line. */}
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {[
                  project && `Project ${project}`,
                  unit === "calls" && "simulated calls",
                  "only these",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>

            {/* What the new scenarios represent. */}
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                What should the new scenarios be?{" "}
                <Box
                  component="span"
                  sx={{ fontWeight: 400, color: "text.tertiary" }}
                >
                  Pick one or more.
                </Box>
              </Typography>
              <Stack spacing={0.75}>
                {TRACE_GOALS.map((g) => {
                  const off = (g.min || 1) > frozenTraces.length;
                  const on = goals.includes(g.id);
                  return (
                    <Box
                      key={g.id}
                      {...clickable(() => !off && toggleGoal(g.id), {
                        role: "checkbox",
                        checked: on,
                      })}
                      sx={{
                        ...focusRing,
                        border: "1px solid",
                        borderColor: on ? "text.primary" : "divider",
                        borderRadius: 1,
                        px: 1.5,
                        py: 0.75,
                        cursor: off ? "not-allowed" : "pointer",
                        opacity: off ? 0.5 : 1,
                      }}
                    >
                      {/* One line each (25 Nov); what it keeps, makes and
                          needs is on hover. */}
                      <Tooltip
                        arrow
                        placement="top-start"
                        enterDelay={400}
                        title={`Keeps: ${g.keeps}. Makes: ${g.makes}. Needs: ${g.needs}.`}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          sx={{ minWidth: 0 }}
                        >
                          <Iconify
                            icon={
                              on
                                ? "solar:check-square-bold"
                                : "solar:stop-linear"
                            }
                            width={16}
                            sx={{
                              flexShrink: 0,
                              color: on ? "text.primary" : "text.disabled",
                            }}
                          />
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              flexShrink: 0,
                            }}
                          >
                            {g.label}
                          </Typography>
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              minWidth: 0,
                            }}
                          >
                            {off ? `Needs ${g.min} or more traces` : g.short}
                          </Typography>
                        </Stack>
                      </Tooltip>
                    </Box>
                  );
                })}
              </Stack>
              {/* A trace that passed has no failure to reproduce — said
                  before anything is created, and drafted as a regression. */}
              {goals.includes("reproduce") && passedTraces.length > 0 && (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
                >
                  {`${passedTraces.length} passed — kept as ${passedTraces.length === 1 ? "a regression" : "regressions"}.`}
                </Typography>
              )}
              {/* Missing evidence, said before anything is created. */}
              {goals.includes("reproduce") && noState.length > 0 && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "status.warning.fg",
                    mt: 0.75,
                  }}
                >
                  {`${noState.length} ${noState.length === 1 ? "has" : "have"} no starting state — drafted as synthetic; add real data at review.`}
                </Typography>
              )}
              {goals.includes("replay") && noTools.length > 0 && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "status.warning.fg",
                    mt: 0.75,
                  }}
                >
                  {`${noTools.length} recorded no tool responses — only the caller's words replay.`}
                </Typography>
              )}
            </Box>

            {/* Where they go. */}
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                Where they go
              </Typography>
              <Stack spacing={1}>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {`Into ${env?.name} — as drafts to review before they join its scenarios.`}
                </Typography>
                <Stack direction="row" spacing={1.5}>
                  {/* No Suite field for now (Chintan, 25 Nov: suites aren't
                      in scope); tags still organise the drafts. */}
                  <TextField
                    size="small"
                    label="Tags"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    placeholder="from-production, refunds"
                    helperText="Comma-separated"
                    sx={{ flex: 1 }}
                  />
                </Stack>
              </Stack>
            </Box>
            <Box>
              <CopyCommandButton
                command={fromTracesCli({
                  env,
                  ids: frozenTraces.map((t) => t.traceId),
                  goal,
                  group: group.trim(),
                })}
                label="Copy as command"
              />
            </Box>
          </Stack>
        </Box>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Button
            size="small"
            onClick={() => (sel ? navigate(-1) : setStep("pick"))}
            sx={{ color: "text.secondary" }}
          >
            {sel ? "Back to Observe" : "Back"}
          </Button>
          <Button
            variant="outlined"
            size="small"
            onClick={saveDraft}
            sx={{ mr: "auto" }}
          >
            Save as draft import
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={create}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          >
            {(() => {
              const n = draftsFor({}).length;
              return `Create ${n} draft${n === 1 ? "" : "s"}${goals.length === 1 ? ` · ${goalMeta?.label.toLowerCase()}` : ""}`;
            })()}
          </Button>
        </Stack>
      </SectionCard>
    );

  return (
    <SectionCard
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {mode === "files" ? "Upload transcript or file" : "From production"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {src === "observe"
              ? `${project || "The agent's Observe traces"} · ${rangeOf(traces)} · personal data already replaced`
              : src === "transcript"
                ? "Upload transcripts with Customer: / Agent: lines — personal data is replaced at review"
                : "Upload CSV or JSON with a prompt column (and expected, if you have it) — a row isn't a whole conversation"}
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="center"
            sx={{
              mt: kinds.length > 1 || src !== "observe" ? 0.75 : 0,
              flexWrap: "wrap",
            }}
          >
            {kinds.length > 1 &&
              kinds
                .map((id) => [id, EVIDENCE_KINDS[id]])
                .map(([id, k]) => (
                  <Button
                    variant="outlined"
                    key={id}
                    size="small"
                    onClick={() => {
                      setSrc(id);
                      setPicked({});
                    }}
                    sx={{
                      px: 1,
                      minWidth: 0,
                      height: 24,
                      typography: "s3",
                      color: src === id ? "text.primary" : "text.secondary",
                      bgcolor: src === id ? "action.selected" : "transparent",
                    }}
                  >
                    {k.label}
                  </Button>
                ))}
            {(src === "transcript" || src === "dataset") && (
              <>
                <input
                  ref={fileRef}
                  hidden
                  multiple
                  type="file"
                  accept={
                    src === "transcript" ? ".txt,.md,.json" : ".csv,.json"
                  }
                  onChange={onUpload}
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => fileRef.current?.click()}
                  startIcon={
                    <Iconify
                      icon="solar:upload-minimalistic-linear"
                      width={14}
                    />
                  }
                  sx={{
                    height: 24,
                    color: "text.primary",
                    borderColor: "divider",
                  }}
                >
                  Upload
                </Button>
              </>
            )}
          </Stack>
        </Box>
        {/* Picking in Observe itself: its table, filters and sessions —
            Create environment test there brings the pick back here. */}
        {mode === "observe" && observeProjectId && (
          <TextLink
            onClick={() =>
              navigate(`${paths.dashboard.observe}/${observeProjectId}`)
            }
          >
            Pick in Observe
          </TextLink>
        )}
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={() => {
            setFrozen(chosen.map((c) => c.id));
            setGoal(
              chosen.every((c) => c.outcome === "passed")
                ? "preserve"
                : "reproduce",
            );
            setStep("freeze");
          }}
          sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 148 }}
        >
          {chosen.length
            ? `Continue with ${chosen.length} ${noun}${chosen.length === 1 ? "" : "s"}`
            : `Pick ${noun}s`}
        </Button>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2.5,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {chosen.length
            ? `${chosen.length} of ${traces.length} picked`
            : `${traces.length} ${noun}${traces.length === 1 ? "" : "s"}${src === "observe" ? " in this range" : ""}`}
        </Typography>
        {traces.length > 0 &&
          (traces.every((c) => picked[c.id]) ? (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              {`All ${traces.length} picked`}
            </Typography>
          ) : (
            <TextLink
              onClick={() =>
                setPicked(Object.fromEntries(traces.map((c) => [c.id, true])))
              }
            >
              {`Select all ${traces.length} matching`}
            </TextLink>
          ))}
      </Stack>
      {traces.length > 0 && <RowHeader />}
      {/* Saved draft imports — finish them later. */}
      {drafts0.length > 0 && (
        <Box
          sx={{
            px: 2.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              mb: 0.25,
            }}
          >
            Draft imports
          </Typography>
          {drafts0.map((d) => (
            <Stack
              key={d.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ py: 0.25 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {d.traceIds.join(", ")}
                </Box>
                {` · ${TRACE_GOALS.find((g) => g.id === d.goal)?.label.toLowerCase() || d.goal} · saved ${relativeTime(d.at)}`}
              </Typography>
              <TextLink onClick={() => resumeDraft(d)}>Resume</TextLink>
              <TextLink onClick={() => dropDraft(d.id)}>Discard</TextLink>
            </Stack>
          ))}
        </Box>
      )}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {traces.length === 0 && (
          <Typography
            sx={{
              typography: "s2",
              color: "text.tertiary",
              px: 2.5,
              py: 4,
              textAlign: "center",
            }}
          >
            {src === "observe"
              ? "No traces from this agent yet."
              : src === "transcript"
                ? "No transcripts yet — upload a .txt, .md or .json file."
                : "No files yet — upload a .csv or .json file."}
          </Typography>
        )}
        {traces.map((c) => (
          <ConversationRow
            key={c.id}
            conv={c}
            draft={drafts.get(c.id)}
            added={alreadyIn.has(drafts.get(c.id).id)}
            picked={!!picked[c.id]}
            onToggle={() => setPicked((p) => ({ ...p, [c.id]: !p[c.id] }))}
          />
        ))}
      </Box>
    </SectionCard>
  );
}
ProductionReplayImport.propTypes = {
  mode: PropTypes.oneOf(["observe", "files"]),
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};

/* One grid for the column header and every row, so the columns line up. */
const ROW_GRID = {
  display: "grid",
  gridTemplateColumns:
    "28px minmax(0, 1fr) 64px 56px 64px minmax(0, 190px) 48px 72px 28px",
  columnGap: 1.5,
  alignItems: "center",
  px: 1.5,
};
const CELL = { typography: "s3", color: "text.secondary", minWidth: 0 };
const NUM = { ...CELL, textAlign: "right", fontVariantNumeric: "tabular-nums" };

function RowHeader() {
  const head = { ...CELL, color: "text.tertiary", fontWeight: 500 };
  return (
    <Box
      sx={{
        ...ROW_GRID,
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <span />
      <Typography sx={head}>Conversation</Typography>
      <Typography sx={head}>Trace</Typography>
      <Typography sx={head}>Agent</Typography>
      <Typography sx={head}>Result</Typography>
      <Typography sx={head}>Missing</Typography>
      <Typography sx={{ ...head, textAlign: "right" }}>Length</Typography>
      <Typography sx={{ ...head, textAlign: "right" }}>When</Typography>
      <span />
    </Box>
  );
}

function ConversationRow({ conv, draft, added, picked, onToggle }) {
  const [open, setOpen] = useState(false);
  const turns = draft.productionReplay?.turns || [];
  const failed = conv.outcome === "failed";
  const result = added
    ? "Added"
    : conv.sourceKind
      ? failed
        ? "Failed"
        : "—"
      : failed
        ? "Failed"
        : "Passed";
  /* The evidence it holds, said by what's missing — a transcript is always there. */
  const missing = [
    !conv.evidence.startingState && "Starting state",
    !conv.evidence.tools && "tool calls",
  ].filter(Boolean);
  const missingText = missing.length
    ? missing.join(", ").replace(/^./, (c) => c.toUpperCase())
    : "—";
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Box
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...ROW_GRID,
          ...focusRing,
          py: 0.75,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box onClick={(e) => e.stopPropagation()}>
          <Checkbox
            size="small"
            checked={picked || added}
            disabled={added}
            onChange={onToggle}
            inputProps={{ "aria-label": `Pick ${conv.topic}` }}
            sx={CHECKBOX_SX}
          />
        </Box>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: 500,
            color: added ? "text.disabled" : "text.primary",
          }}
        >
          {conv.topic}
        </Typography>
        <Typography noWrap sx={{ ...CELL, fontFamily: MONO }}>
          {conv.traceId || "—"}
        </Typography>
        <Typography noWrap sx={CELL}>
          {conv.agentVersion || "—"}
        </Typography>
        <Typography
          noWrap
          sx={{
            ...CELL,
            color: failed && !added ? "status.danger.fg" : CELL.color,
          }}
        >
          {result}
        </Typography>
        <Typography
          noWrap
          title={
            missing.length ? `No ${missing.join(" or ")} in this trace` : ""
          }
          sx={{
            ...CELL,
            color: missing.length ? "status.warning.fg" : "text.disabled",
          }}
        >
          {missingText}
        </Typography>
        <Typography noWrap sx={NUM}>
          {conv.durationS ? fmtDuration(conv.durationS) : "—"}
        </Typography>
        <Typography noWrap sx={NUM}>
          {relativeTime(conv.at)}
        </Typography>
        <Tooltip
          title={open ? "Hide the caller's turns" : "Show the caller's turns"}
          arrow
        >
          <IconButton size="small" tabIndex={-1}>
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
      </Box>
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={0.5}
          sx={{ px: 6, py: 1.5, bgcolor: "background.neutral" }}
        >
          {/* The rest of what the trace holds, kept off the row. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ pb: 0.5, flexWrap: "wrap" }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {[
                conv.channel,
                conv.spans ? `${conv.spans} spans` : null,
                conv.sourceKind && !failed && conv.expected
                  ? `expected: ${conv.expected}`
                  : null,
                [
                  "transcript",
                  conv.evidence.tools ? "tool calls" : null,
                  conv.evidence.startingState ? "starting state" : null,
                ]
                  .filter(Boolean)
                  .join(", "),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
            <PersonalDataTag row={draft} />
          </Stack>
          {turns.map((t) => (
            <Stack
              key={t.n}
              direction="row"
              spacing={1.25}
              alignItems="baseline"
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.disabled",
                  width: 52,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`Caller ${t.n}`}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <MarkedFakes text={t.marked || t.text} />
              </Typography>
            </Stack>
          ))}
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", pt: 0.5 }}
          >
            {`Persona inferred: ${draft.persona?.name} · ${draft.persona?.archetype}. The agent's side isn't replayed.`}
          </Typography>
        </Stack>
      </Collapse>
    </Box>
  );
}
/* A replayed turn with each fake value (put in place of personal data)
   underlined, so it doesn't read as the real caller's details. */
function MarkedFakes({ text }) {
  const parts = String(text).split(
    new RegExp(`${FAKE_OPEN}(.*?)${FAKE_CLOSE}`, "g"),
  );
  return parts.map((part, i) =>
    i % 2 ? (
      <Box
        key={i}
        component="span"
        title="Replaced with a fake value"
        sx={{
          color: "text.primary",
          textDecoration: "underline dotted",
          textUnderlineOffset: "3px",
          cursor: "help",
        }}
      >
        {part}
      </Box>
    ) : (
      part
    ),
  );
}
MarkedFakes.propTypes = { text: PropTypes.string };

ConversationRow.propTypes = {
  conv: PropTypes.object,
  draft: PropTypes.object,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};
