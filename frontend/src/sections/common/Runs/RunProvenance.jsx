// What a run used (R4-SPEC D7, implementation). One component, three
// variants on F's provenanceModel (never a second model):
//   line  — F's `provenanceLine` string, one line, truncating from the right
//           (full text in the tooltip); click opens the card in a popover.
//   chips — the same facts as chips (`maxChips`, `+n` opens the card).
//   card  — `What this run used`: models (declared / seen dots), tools (amber
//           dot on declared-but-unused / used-but-undeclared), MCP, prompts,
//           parameters (changed from the code default in bold), where it ran
//           (+ owner), code (sha, dirty files, diff snapshot, tries per sha),
//           voice, harness (+ capture), safety, model keys, endpoint; actions
//           Rerun with changes… (⇧R), Copy config as JSON, Open trace project.
//           With `compareTo`, changed sections are tinted amber and listed first.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  Divider,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import CopyButton from "src/sections/common/ux/CopyButton";
import CopyId from "src/sections/common/ux/CopyId";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import Term from "src/sections/common/ux/Term";
import { VOCAB } from "src/sections/common/vocab";
import { useVoiceSummary } from "src/api/develop/r4";
import {
  agentVersionOf,
  provenanceLine,
  provenanceOf,
} from "./provenanceModel";
import {
  changedSections,
  chipsOf,
  codeRows,
  harnessRows,
  keysText,
  modelRows,
  paramRows,
  safetyText,
  toolRows,
  voiceRows,
  whereRows,
} from "./provenanceExtras";
import { canRerunHere } from "./runKind";
import {
  modelParamRows,
  provenanceJsonOf,
  runSettingsRows,
} from "./provenanceJson";

const runIdOf = (run) => run?.run_id ?? run?.id ?? null;
const datasetOf = (run) => run?.dataset_id ?? run?.datasetId ?? null;

/** Tries per code sha for a code run (implementation's voice-summary read carries them). */
function useTriesBySha(run, enabled) {
  const p = useMemo(() => (run ? provenanceOf(run) : null), [run]);
  const want = Boolean(enabled && p?.code?.sha && datasetOf(run));
  const { data } = useVoiceSummary(datasetOf(run), {
    runIds: [runIdOf(run)],
    enabled: want,
  });
  if (!want) return null;
  const r = (data?.runs ?? []).find((x) => x.run_id === runIdOf(run));
  return r?.tries?.by_sha ?? null;
}

const Dot = ({ on, title, color = "text.secondary" }) => (
  <CustomTooltip show size="small" title={title}>
    <Box
      component="span"
      aria-label={title}
      sx={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        flexShrink: 0,
        border: "1.5px solid",
        borderColor: color,
        bgcolor: on ? color : "transparent",
        display: "inline-block",
      }}
    />
  </CustomTooltip>
);
Dot.propTypes = {
  on: PropTypes.bool,
  title: PropTypes.string,
  color: PropTypes.string,
};

const WarnDot = ({ title }) => (
  <CustomTooltip show size="small" title={title}>
    <Box
      component="span"
      aria-label={title}
      data-provenance-warning
      sx={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        bgcolor: "warning.main",
        display: "inline-block",
        flexShrink: 0,
      }}
    />
  </CustomTooltip>
);
WarnDot.propTypes = { title: PropTypes.string };

/** One titled block of the card. */
function Sec({ title, term, changed, children }) {
  return (
    <Box
      data-provenance-section={title}
      sx={{
        px: 1.5,
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: changed
          ? (t) => alpha(t.palette.warning.main, 0.12)
          : undefined,
      }}
    >
      <Stack direction="row" alignItems="baseline" gap={0.75} sx={{ mb: 0.5 }}>
        <Typography
          typography="s3"
          fontWeight="fontWeightSemiBold"
          color="text.secondary"
        >
          {term ? <Term k={term}>{title}</Term> : title}
        </Typography>
        {changed ? (
          <Typography
            typography="s3"
            color="warning.dark"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {`Changed: ${changed.join(" · ")}`}
          </Typography>
        ) : null}
      </Stack>
      {children}
    </Box>
  );
}
Sec.propTypes = {
  title: PropTypes.string,
  term: PropTypes.string,
  changed: PropTypes.arrayOf(PropTypes.string),
  children: PropTypes.node,
};

/** `Label   value` rows. */
function Rows({ rows }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "104px minmax(0, 1fr)",
        columnGap: 1,
        rowGap: 0.25,
      }}
    >
      {rows.map((r) => (
        <React.Fragment key={r.label}>
          <Typography typography="s2" color="text.secondary" noWrap>
            {r.term ? <Term k={r.term}>{r.label}</Term> : r.label}
          </Typography>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ minWidth: 0 }}
          >
            {/* options-truth L7b G4 (connect:pill): the machine a run ran
                on is recorded text (connecting a machine is absent): no
                live dot, status, poll or teammates line. */}
            {r.copy ? (
              <CopyId id={String(r.copy)} short={7} label="sha" />
            ) : (
              <Typography
                typography="s2"
                color={r.warn ? "warning.dark" : "text.primary"}
                sx={{
                  minWidth: 0,
                  wordBreak: "break-word",
                  fontFamily: r.mono
                    ? "ui-monospace, SFMono-Regular, Menlo, monospace"
                    : undefined,
                }}
              >
                {r.value}
              </Typography>
            )}
            {r.note ? (
              <CustomTooltip show size="small" title={r.note}>
                <Iconify
                  icon="mdi:information-outline"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </CustomTooltip>
            ) : null}
          </Stack>
          {r.files?.length ? (
            <>
              <span />
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                }}
              >
                {r.files.join(", ")}
              </Typography>
            </>
          ) : null}
        </React.Fragment>
      ))}
    </Box>
  );
}
Rows.propTypes = { rows: PropTypes.array };

/** The card body (`What this run used`). */
export function ProvenanceCard({ run, compareTo, onRerun, tries, sx }) {
  const p = useMemo(() => provenanceOf(run), [run]);
  const runId = runIdOf(run);
  const fetched = useTriesBySha(run, !tries);
  const bySha = tries ?? fetched;
  const changed = useMemo(
    () => changedSections(compareTo, run),
    [compareTo, run],
  );
  const version = agentVersionOf(run);
  const models = modelRows(p);
  const tools = toolRows(p);
  // L7c g5: declared params, else the model params it ran with; the run's
  // settings (tries, at a time, caps, data scope, eval versions, reducer)
  // are their own section, read from the run record.
  const declared = paramRows(p);
  const params = declared.length ? declared : modelParamRows(run?.config);
  const settings = useMemo(() => runSettingsRows(run), [run]);
  const code = codeRows(p, bySha);
  const voice = voiceRows(p, run);
  const harness = harnessRows(p);
  const safety = safetyText(p);
  const keys = keysText(p);
  const where = whereRows(p);
  const traceProject = run?.trace_project_id ?? null;

  const sections = [
    models.length && {
      id: "models",
      node: (
        <Sec key="models" title="Models" changed={changed.get("models")}>
          <Stack gap={0.25}>
            {models.map((m) => (
              <Stack
                key={`${m.id}:${m.role}`}
                direction="row"
                alignItems="center"
                gap={0.75}
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  noWrap
                >
                  {m.id}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  noWrap
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  {[m.role, m.calls != null ? `${m.calls} calls` : null]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
                <Dot
                  on={m.declared}
                  title={
                    m.declared
                      ? "Declared by the agent"
                      : "Not declared by the agent"
                  }
                />
                <Dot
                  on={m.seen}
                  color="primary.main"
                  title={
                    m.seen
                      ? "Seen in this run"
                      : "Not seen in this run's traces"
                  }
                />
              </Stack>
            ))}
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Dot on title="Declared by the agent" />
              <Typography typography="s3" color="text.disabled">
                declared
              </Typography>
              <Dot on color="primary.main" title="Seen in this run's traces" />
              <Typography typography="s3" color="text.disabled">
                seen in this run
              </Typography>
            </Stack>
          </Stack>
        </Sec>
      ),
    },
    tools.length && {
      id: "tools",
      node: (
        <Sec
          key="tools"
          title={`Tools · ${tools.length}`}
          changed={changed.get("tools")}
        >
          <Stack direction="row" gap={0.5} sx={{ flexWrap: "wrap" }}>
            {tools.map((t) => (
              <Stack
                key={t.name}
                direction="row"
                alignItems="center"
                gap={0.5}
                sx={{
                  px: 0.75,
                  height: 22,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: t.warning ? "warning.main" : "divider",
                }}
              >
                {t.warning ? <WarnDot title={t.warning} /> : null}
                <Typography
                  typography="s2"
                  sx={{
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                  }}
                >
                  {t.name}
                </Typography>
                {t.sideEffect ? (
                  <CustomTooltip
                    show
                    size="small"
                    title="Has side effects (changes a real system)"
                  >
                    <Iconify
                      icon="mdi:alert-octagon-outline"
                      width={13}
                      sx={{ color: "warning.main" }}
                    />
                  </CustomTooltip>
                ) : null}
              </Stack>
            ))}
          </Stack>
        </Sec>
      ),
    },
    p.mcp.length && {
      id: "mcp",
      node: (
        <Sec
          key="mcp"
          title="MCP servers"
          term="mcp"
          changed={changed.get("mcp")}
        >
          <Rows
            rows={p.mcp.map((m) => ({
              label: m.name,
              value:
                [
                  m.transport,
                  m.tools != null ? `${m.tools} tools` : null,
                  m.permission ? `permission ${m.permission}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || "—",
            }))}
          />
        </Sec>
      ),
    },
    p.prompts.length && {
      id: "prompts",
      node: (
        <Sec key="prompts" title="Prompts" changed={changed.get("prompts")}>
          <Stack gap={0.25}>
            {p.prompts.map((pr) => (
              <Stack
                key={pr.name}
                direction="row"
                alignItems="center"
                gap={0.75}
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  noWrap
                >
                  {pr.name}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  noWrap
                  sx={{ flex: 1 }}
                >
                  {[
                    pr.chars != null ? `${pr.chars} chars` : null,
                    pr.text_hash ? `hash ${pr.text_hash}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
                <CustomTooltip
                  show
                  size="small"
                  title="The prompt text was not uploaded with this run; it keeps its length and hash."
                >
                  <span>
                    <Button
                      size="small"
                      disabled
                      sx={{ minWidth: 0, px: 0.5, height: 22 }}
                    >
                      Open
                    </Button>
                  </span>
                </CustomTooltip>
              </Stack>
            ))}
          </Stack>
        </Sec>
      ),
    },
    params.length && {
      id: "params",
      node: (
        <Sec
          key="params"
          title="Parameters"
          term="declaredParam"
          changed={changed.get("params")}
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "120px minmax(0,1fr)",
              columnGap: 1,
              rowGap: 0.25,
            }}
          >
            {params.map((r) => (
              <React.Fragment key={r.name}>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  noWrap
                  sx={{
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                  }}
                >
                  {r.name}
                </Typography>
                <CustomTooltip
                  show
                  size="small"
                  title={
                    r.changed
                      ? `Changed from the code default (${r.defaultText})`
                      : r.full
                  }
                >
                  <Typography
                    typography="s2"
                    fontWeight={r.changed ? "fontWeightBold" : undefined}
                    noWrap
                  >
                    {`= ${r.value}`}
                  </Typography>
                </CustomTooltip>
              </React.Fragment>
            ))}
          </Box>
        </Sec>
      ),
    },
    // L7c g5 (identity:provenance-card): tries, at a time, caps, data
    // scope, eval versions and reducer, from the run record.
    settings.length && {
      id: "settings",
      node: (
        <Sec key="settings" title="Run settings">
          <Rows rows={settings} />
        </Sec>
      ),
    },
    where.length && {
      id: "where",
      node: (
        <Sec
          key="where"
          title={VOCAB.WHERE_IT_RUNS.replace("runs", "ran")}
          term="executionMode"
          changed={changed.get("where")}
        >
          <Rows rows={where} />
        </Sec>
      ),
    },
    code.length && {
      id: "code",
      node: (
        <Sec key="code" title="Code" changed={changed.get("code")}>
          <Rows rows={code} />
        </Sec>
      ),
    },
    voice.length && {
      id: "voice",
      node: (
        <Sec key="voice" title="Voice" changed={changed.get("voice")}>
          <Rows rows={voice} />
        </Sec>
      ),
    },
    harness.length && {
      id: "harness",
      node: (
        <Sec
          key="harness"
          title="Harness"
          term="harness"
          changed={changed.get("harness")}
        >
          <Rows rows={harness} />
        </Sec>
      ),
    },
    safety && {
      id: "safety",
      node: (
        <Sec
          key="safety"
          title="Safety"
          term="sideEffects"
          changed={changed.get("safety")}
        >
          <Typography
            typography="s2"
            color={safety.warn ? "warning.dark" : "text.primary"}
          >
            {safety.text}
          </Typography>
        </Sec>
      ),
    },
    keys && {
      id: "keys",
      node: (
        <Sec key="keys" title="Model API keys">
          <Typography typography="s2">{keys}</Typography>
        </Sec>
      ),
    },
    p.endpoint && {
      id: "endpoint",
      node: (
        <Sec key="endpoint" title="Endpoint" changed={changed.get("endpoint")}>
          <Rows
            rows={[
              {
                label: "Protocol",
                value: String(p.endpoint.protocol ?? "http").toUpperCase(),
              },
              { label: "URL", value: p.endpoint.url ?? "—", mono: true },
            ]}
          />
        </Sec>
      ),
    },
  ].filter(Boolean);
  // Changed sections first (D7), the rest in the card's order.
  const ordered = [
    ...sections.filter((s) => changed.has(s.id)),
    ...sections.filter((s) => !changed.has(s.id)),
  ];
  // L7c g5: the config plus the data scope, eval versions, reducer and
  // metadata the run record carries.
  const json = () => JSON.stringify(provenanceJsonOf(run), null, 2);
  const rerun = () => {
    if (onRerun) onRerun(run);
    else openRerunWithChanges({ runId });
  };

  return (
    <Box
      data-run-provenance="card"
      sx={{ width: 460, maxWidth: "100%", ...sx }}
    >
      <Stack
        gap={0.5}
        sx={{
          px: 1.5,
          pt: 1.25,
          pb: 1,
          bgcolor: changed.has("header")
            ? (t) => alpha(t.palette.warning.main, 0.12)
            : undefined,
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.75}>
          {p.source.entryId ? (
            <SourceLogo entryId={p.source.entryId} size={20} />
          ) : null}
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {VOCAB.WHAT_THIS_RUN_USES.replace("uses", "used")}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0, flex: 1 }}
          >
            {[run?.label, run?.name].filter(Boolean).join(" ")}
          </Typography>
          {version ? (
            <Chip
              size="small"
              label={version.text}
              variant="outlined"
              sx={{ height: 20, typography: "s3" }}
            />
          ) : null}
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ minWidth: 0 }}
        >
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ minWidth: 0 }}
          >
            {provenanceLine(p)}
          </Typography>
        </Stack>
        {changed.get("header") ? (
          <Typography typography="s3" color="warning.dark">
            {`Changed: ${changed.get("header").join(" · ")}`}
          </Typography>
        ) : null}
      </Stack>
      <Box sx={{ maxHeight: "min(60vh, 520px)", overflowY: "auto" }}>
        {ordered.map((s) => s.node)}
      </Box>
      <Divider />
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ px: 1, py: 0.75 }}
      >
        {/* dataset integration FE-0 (owner Q12): absent for a code, harness, voice or
            upload run (no real path to rerun it from here until FE-1). */}
        {run && !canRerunHere(run) ? null : (
          <KeyHintTooltip
            title={VOCAB.RERUN_WITH_CHANGES}
            action="rerunWithChanges"
          >
            {/* span: a disabled button fires no events for the tooltip */}
            <span style={{ display: "inline-flex", flexShrink: 0 }}>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:restart" width={16} />}
                onClick={rerun}
                disabled={!runId}
                sx={{ whiteSpace: "nowrap", flexShrink: 0, height: 28 }}
              >
                {VOCAB.RERUN_WITH_CHANGES}
              </Button>
            </span>
          </KeyHintTooltip>
        )}
        <Box sx={{ flex: 1 }} />
        <Stack direction="row" alignItems="center" sx={{ flexShrink: 0 }}>
          <CopyButton
            value={json}
            tooltip="Copy config as JSON"
            toast="Config copied as JSON"
          />
          <Typography typography="s3" color="text.secondary" noWrap>
            JSON
          </Typography>
        </Stack>
        {/* L7c g5: a run with no trace project shows no Trace project. */}
        {traceProject ? (
          <Button
            size="small"
            component={RouterLink}
            to={`/dashboard/observe/${traceProject}/llm-tracing`}
            endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
            sx={{ height: 28, whiteSpace: "nowrap", flexShrink: 0 }}
          >
            Trace project
          </Button>
        ) : null}
      </Stack>
    </Box>
  );
}

ProvenanceCard.propTypes = {
  run: PropTypes.object.isRequired,
  compareTo: PropTypes.object,
  onRerun: PropTypes.func,
  tries: PropTypes.array,
  sx: PropTypes.object,
};

function CardPopover({ anchor, onClose, ...rest }) {
  return (
    <Popover
      open={Boolean(anchor)}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{ paper: { sx: { mt: 0.5, overflow: "hidden" } } }}
      onClick={(e) => e.stopPropagation()}
    >
      {anchor ? <ProvenanceCard {...rest} /> : null}
    </Popover>
  );
}
CardPopover.propTypes = { anchor: PropTypes.any, onClose: PropTypes.func };

export default function RunProvenance({
  run,
  variant = "line",
  compareTo,
  maxChips = 4,
  onRerun,
  tries,
  sx,
  interactive = true,
  text,
}) {
  const p = useMemo(() => (run ? provenanceOf(run) : null), [run]);
  const [anchor, setAnchor] = useState(null);
  if (!p) return null;
  const open = (e) => {
    e.stopPropagation();
    e.preventDefault();
    setAnchor(e.currentTarget);
  };
  const popover = (
    <CardPopover
      anchor={anchor}
      onClose={() => setAnchor(null)}
      run={run}
      compareTo={compareTo}
      onRerun={onRerun}
      tries={tries}
    />
  );

  if (variant === "card")
    return (
      <ProvenanceCard
        run={run}
        compareTo={compareTo}
        onRerun={onRerun}
        tries={tries}
        sx={sx}
      />
    );

  if (variant === "chips") {
    const chips = chipsOf(p);
    const shown = chips.slice(0, maxChips);
    const rest = chips.length - shown.length;
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        data-run-provenance="chips"
        sx={{ minWidth: 0, overflow: "hidden", flexWrap: "nowrap", ...sx }}
      >
        {shown.map((c, i) => (
          <CustomTooltip
            key={c.key}
            show
            size="small"
            title={c.warnText ?? c.tooltip ?? c.text}
          >
            <Chip
              size="small"
              variant="outlined"
              clickable
              onClick={open}
              icon={
                i === 0 && p.source.entryId ? (
                  <Box
                    component="span"
                    sx={{ display: "inline-flex", ml: 0.5 }}
                  >
                    <SourceLogo entryId={p.source.entryId} size={16} />
                  </Box>
                ) : c.warn ? (
                  <Box
                    component="span"
                    sx={{ display: "inline-flex", ml: 0.75 }}
                  >
                    <WarnDot title={c.warnText} />
                  </Box>
                ) : undefined
              }
              label={c.text}
              sx={{
                height: 22,
                typography: "s3",
                maxWidth: 200,
                flexShrink: i === 0 ? 0 : 1,
                "& .MuiChip-label": { px: 0.75 },
              }}
            />
          </CustomTooltip>
        ))}
        {rest > 0 ? (
          <Chip
            size="small"
            variant="outlined"
            clickable
            onClick={open}
            label={`+${rest}`}
            aria-label={`${rest} more: open what this run used`}
            sx={{ height: 22, typography: "s3", flexShrink: 0 }}
          />
        ) : null}
        {popover}
      </Stack>
    );
  }

  // IT-R2 G2 P7 (additive): `text` replaces the visible words (a Matrix
  // header shows only the model); the tooltip and the card stay whole.
  const line = provenanceLine(p);
  const shown = text || line;
  const warn = p.warnings.length > 0;
  // IT-R1 G2 P10: the line's `+1` is named in the tooltip.
  const models = [...new Set((p.models ?? []).map((m) => m.id))];
  const named = models.length > 1 ? ` · models: ${models.join(", ")}` : "";
  return (
    <>
      <CustomTooltip
        show
        title={
          interactive
            ? `${line}${named} · click for everything this run used`
            : `${line}${named}`
        }
        placement="top"
        size="small"
      >
        <ButtonBase
          component={interactive ? "button" : "span"}
          disabled={!interactive}
          role={interactive ? undefined : "none"}
          data-run-provenance="line"
          onClick={interactive ? open : undefined}
          aria-haspopup="dialog"
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            minWidth: 0,
            maxWidth: "100%",
            borderRadius: 0.5,
            px: 0.25,
            justifyContent: "flex-start",
            "&:hover .rp-line": {
              color: "text.primary",
              textDecoration: "underline dotted",
            },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
            ...sx,
          }}
        >
          {p.source.entryId ? (
            <SourceLogo entryId={p.source.entryId} size={16} />
          ) : null}
          <Typography
            component="span"
            className="rp-line"
            typography="s2"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0, textUnderlineOffset: "3px" }}
          >
            {shown}
          </Typography>
          {warn ? (
            <WarnDot title={p.warnings.map((w) => w.text).join(" · ")} />
          ) : null}
        </ButtonBase>
      </CustomTooltip>
      {popover}
    </>
  );
}

RunProvenance.propTypes = {
  text: PropTypes.string,
  run: PropTypes.object,
  variant: PropTypes.oneOf(["line", "chips", "card"]),
  compareTo: PropTypes.object,
  maxChips: PropTypes.number,
  onRerun: PropTypes.func,
  // Lane X additive: tries per code sha `[{sha, n}]` when the caller has
  // them (else the card reads them), and outer sx.
  tries: PropTypes.array,
  sx: PropTypes.object,
  // false inside another button (picker rows): plain text, no card.
  interactive: PropTypes.bool,
};
