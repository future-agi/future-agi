import PropTypes from "prop-types";
import { useMemo, useState } from "react";
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
import { SectionCard } from "../../components/primitives";
import { replayScenarioOf, asProposal } from "../../_mock/scenarioSources";
import {
  TRACE_GOALS,
  fromTracesCli,
  productionTracesOf,
  rangeOf,
  traceDraftsOf,
  traceProjectOf,
} from "../../_mock/traceImport";
import { ensureTag, groupPatch, groupsOf } from "../../_mock/scenarioOrg";
import { useEnvState } from "../../store";
import CopyCommandButton from "../../components/CopyCommandButton";
import { TextLink } from "../../agents/connect/connectBits";
import { relativeTime } from "../../_mock/scenarioProvenance";
import { PersonalDataTag } from "./ScenarioStateBits";
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
 * ID, agent version, outcome and the evidence it actually holds; "select all
 * matching" is its own counted choice. Freeze: the picked IDs are fixed (new
 * traces or a changed filter don't change the import), then what the new
 * scenarios should represent and where they go. Drafts then wait for review.
 */
export default function ProductionReplayImport({
  env,
  envState,
  onAdd,
  selected,
}) {
  const traces = useMemo(
    () => productionTracesOf(env, envState),
    [env, envState],
  );
  const project = traceProjectOf(env, envState);
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
  const [step, setStep] = useState("pick");
  const [frozen, setFrozen] = useState([]);
  const [goal, setGoal] = useState("reproduce");
  const [group, setGroup] = useState("");
  const [tags, setTags] = useState("");
  const { patch } = useEnvState(env?.id);
  const chosen = traces.filter((c) => picked[c.id]);
  const frozenTraces = traces.filter((t) => frozen.includes(t.id));
  const goalMeta = TRACE_GOALS.find((g) => g.id === goal);
  const noState = frozenTraces.filter((t) => !t.evidence.startingState);
  const noTools = frozenTraces.filter((t) => !t.evidence.tools);
  const groups = groupsOf(envState);

  const create = () => {
    const importInfo = {
      id: `imp_${Date.now().toString(36)}`,
      at: new Date().toISOString(),
      project,
      range: rangeOf(frozenTraces),
      ids: frozenTraces.map((t) => t.traceId),
    };
    const rows = traceDraftsOf(frozenTraces, goal, env, importInfo).filter(
      (r) => !alreadyIn.has(r.id),
    );
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
        asProposal(tagIds.length ? { ...r, tagIds } : r, "production"),
      ),
    );
  };

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
                  {`${frozenTraces.length} trace${frozenTraces.length === 1 ? "" : "s"} frozen`}
                </Typography>
                <TextLink onClick={() => setStep("pick")}>
                  Change selection
                </TextLink>
              </Stack>
              <Typography sx={{ typography: "s2", fontFamily: MONO, mt: 0.25 }}>
                {frozenTraces.map((t) => t.traceId).join(", ")}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {`Project ${project} · ${rangeOf(frozenTraces)} · only these traces — new ones arriving or a changed filter won't change this import`}
              </Typography>
            </Box>

            {/* What the new scenarios represent. */}
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                What should the new scenarios be?
              </Typography>
              <Stack spacing={0.75}>
                {TRACE_GOALS.map((g) => {
                  const off = (g.min || 1) > frozenTraces.length;
                  const on = goal === g.id;
                  return (
                    <Box
                      key={g.id}
                      {...clickable(() => !off && setGoal(g.id), {
                        role: "radio",
                        checked: on,
                      })}
                      sx={{
                        ...focusRing,
                        border: "1px solid",
                        borderColor: on ? "text.primary" : "divider",
                        borderRadius: 1,
                        px: 1.5,
                        py: 1,
                        cursor: off ? "not-allowed" : "pointer",
                        opacity: off ? 0.5 : 1,
                      }}
                    >
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {g.label}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {`Keeps: ${g.keeps}. Makes: ${g.makes}.`}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {off
                          ? `Needs ${g.min} or more traces`
                          : `Needs: ${g.needs}`}
                      </Typography>
                    </Box>
                  );
                })}
              </Stack>
              {/* Missing evidence, said before anything is created. */}
              {goal === "reproduce" && noState.length > 0 && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "status.warning.fg",
                    mt: 0.75,
                  }}
                >
                  {`${noState.map((t) => t.traceId).join(", ")} ${noState.length === 1 ? "has" : "have"} no starting state — at review you add it, replay the recorded responses instead, or keep ${noState.length === 1 ? "it" : "them"} blocked. Never called an exact reproduction.`}
                </Typography>
              )}
              {goal === "replay" && noTools.length > 0 && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "status.warning.fg",
                    mt: 0.75,
                  }}
                >
                  {`${noTools.map((t) => t.traceId).join(", ")} recorded no tool responses — only the caller's words can be replayed.`}
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
                  {`Into ${env?.name} — as drafts to review before they join the suite.`}
                </Typography>
                <Stack direction="row" spacing={1.5}>
                  <TextField
                    size="small"
                    label="Group"
                    value={group}
                    onChange={(e) => setGroup(e.target.value)}
                    placeholder={groups[0]?.name || "e.g. Refund regressions"}
                    helperText={
                      groups.length
                        ? `Existing: ${groups
                            .slice(0, 3)
                            .map((g) => g.name)
                            .join(", ")}`
                        : "A new group is made if it doesn't exist"
                    }
                    sx={{ flex: 1 }}
                  />
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
            onClick={() => setStep("pick")}
            sx={{ color: "text.secondary" }}
          >
            Back
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={create}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          >
            {(() => {
              const n = traceDraftsOf(frozenTraces, goal, env, {}).length;
              return `Create ${n} draft${n === 1 ? "" : "s"} · ${goalMeta?.label.toLowerCase()}`;
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
            Production traces
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Observe project ${project} · ${rangeOf(traces)} · pick the exact traces — personal data is already replaced`}
          </Typography>
        </Box>
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
            ? `Continue with ${chosen.length} trace${chosen.length === 1 ? "" : "s"}`
            : "Pick traces"}
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
            : `${traces.length} traces in this range`}
        </Typography>
        <Button
          size="small"
          disabled={traces.every((c) => picked[c.id])}
          onClick={() =>
            setPicked(Object.fromEntries(traces.map((c) => [c.id, true])))
          }
          sx={{
            color: "primary.main",
            minWidth: 0,
            "&.Mui-disabled": { color: "text.disabled" },
          }}
        >
          {`Select all ${traces.length} matching`}
        </Button>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
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
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};

function ConversationRow({ conv, draft, added, picked, onToggle }) {
  const [open, setOpen] = useState(false);
  const turns = draft.productionReplay?.turns || [];
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...focusRing,
          px: 1.5,
          py: 1,
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
            sx={CHECKBOX_SX}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
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
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ mt: 0.25, minWidth: 0 }}
          >
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              {`${conv.traceId} · agent ${conv.agentVersion} · ${conv.channel} · ${fmtDuration(conv.durationS)} · ${relativeTime(conv.at)} · ${conv.spans} spans${conv.outcome === "failed" ? " · failed in production" : " · passed"}`}
            </Typography>
            <PersonalDataTag row={draft} />
            {/* What this trace actually holds — missing evidence named up front. */}
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              {[
                "transcript",
                conv.evidence.tools ? "tool calls" : null,
                conv.evidence.startingState ? "starting state" : null,
              ]
                .filter(Boolean)
                .join(" · ")}
              {!conv.evidence.startingState && (
                <Box component="span" sx={{ color: "status.warning.fg" }}>
                  {` · no starting state${conv.evidence.tools ? "" : " or tool calls"}`}
                </Box>
              )}
            </Typography>
            {added && (
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                · already added
              </Typography>
            )}
          </Stack>
        </Box>
        <Tooltip
          title={open ? "Hide the caller's turns" : "Show the caller's turns"}
          arrow
        >
          <IconButton size="small">
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
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={0.5}
          sx={{ px: 6, py: 1.5, bgcolor: "background.neutral" }}
        >
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
                {t.text}
              </Typography>
            </Stack>
          ))}
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", pt: 0.5 }}
          >
            {`Persona inferred: ${draft.persona?.name} · ${draft.persona?.archetype}. The agent's side isn't replayed — your agent answers for itself.`}
          </Typography>
        </Stack>
      </Collapse>
    </Box>
  );
}
ConversationRow.propTypes = {
  conv: PropTypes.object,
  draft: PropTypes.object,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};
