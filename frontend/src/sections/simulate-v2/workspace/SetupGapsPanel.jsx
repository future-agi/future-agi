import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "@mui/material/styles";
import { statusColors } from "src/utils/runStatus";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  Collapse,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import {
  setupGaps,
  sortGaps,
  gapMeter,
  gapCoverage,
  GAP_STATUS,
  credentialManifest,
  answerGapPatch,
  reopenGapPatch,
} from "../_mock/setupGaps";
import {
  downgradeTierPatch,
  safeTargetPatch,
  toolSettingOf,
} from "../_mock/toolEnv";
import { openGapsInbox } from "../_mock/gapsInboxBus";
import { SafeTargetPrompt } from "./ToolDial";
import { credentialRefPatch } from "../_mock/secretRefs";
import { credentialSetPatch } from "../_mock/envConfig";
import {
  SecretRefText,
  SetRefAction,
  SecretRefEditor,
  PreflightLine,
  RefTag,
} from "./SecretRefControls";
import { NudgesSection } from "./NudgeCard";
import { LiveConsentCard } from "../agents/AgentLiveConsent";
import { InfoTip } from "../agents/AgentBits";
import { useConnectedAgents } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { useSnackbar } from "notistack";
import { openNudgesOf } from "../_mock/decoupled";
import { clickable, focusRing } from "../components/clickable";

/**
 * Needs input (PRD v6 Story 3).
 *
 * Reading an agent gets most of the way and then hits things no amount of
 * reading can settle. Each of those is a card: what is missing, why it
 * matters, the default we'd use, and three ways out — accept the default
 * (stamped assumed), give your own answer (stamped confirmed), or, for an
 * optional one, skip it and let the scenarios resting on it sit the run out.
 * Blocking cards hold the run; optional ones only cost coverage.
 *
 * Colour carries one fact: red is blocking. Everything else — optional,
 * assumed, confirmed, skipped — is the same neutral mark with a different
 * glyph, and the word sits beside it.
 */
export default function SetupGapsPanel({
  env,
  envState,
  patch,
  onGo,
  onClose,
  focusId = null,
}) {
  const gaps = sortGaps(setupGaps(env, envState));
  const meter = gapMeter(gaps);
  const coverage = gapCoverage(env, envState, gaps);
  const credentials = credentialManifest(env, envState);

  const blocking = gaps.filter((g) => g.status === "blocking");
  const waiting = gaps.filter((g) => g.status === "optional");
  const settled = gaps.filter(
    (g) => g.status !== "blocking" && g.status !== "optional",
  );
  const go = (to) => {
    onClose?.();
    onGo?.(to);
  };

  const cardProps = { env, envState, patch, onGo: go, coverage };
  const openNudges = openNudgesOf(envState).length;

  const set = credentials.filter((c) => c.status === "configured").length;
  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={2}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Needs input
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {openNudges > 0
              ? "Your agent changed — review that first, then what we couldn't work out from your agent."
              : "What we couldn't work out from your agent. Blocking items hold a run; optional ones only cost coverage."}
          </Typography>
        </Box>
        {onClose && (
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify
              icon="solar:close-linear"
              width={20}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
        )}
      </Stack>

      <Box sx={{ flex: 1, overflowY: "auto" }}>
        {/* A new agent version's nudges come first — they're why the
            environment reads Drifted. */}
        <NudgesSection
          env={env}
          envState={envState}
          patch={patch}
          focusId={focusId}
          onNavigate={onClose}
        />

        <Stack spacing={2.5} sx={{ px: 2.5, py: 2.25 }}>
          {gaps.length > 0 && <Figures meter={meter} coverage={coverage} />}

          {gaps.length === 0 ? (
            <EmptyState
              icon="solar:inbox-linear"
              title="Nothing to resolve"
              body="Connect an agent and the builder will tell you what it could not work out on its own."
            />
          ) : (
            <>
              <GapGroup
                label="Blocking"
                note="A run can't start until these are answered"
                gaps={blocking}
                focusId={focusId}
                {...cardProps}
              />
              <GapGroup
                label="Waiting on you"
                note="A run can start; what rests on these sits out"
                gaps={waiting}
                focusId={focusId}
                {...cardProps}
              />
            </>
          )}

          {credentials.length > 0 && (
            <Part
              label="Credentials"
              count={`${set} of ${credentials.length} set`}
              note="Values stay in your secret manager"
            >
              <CredentialsList
                credentials={credentials}
                envState={envState}
                patch={patch}
              />
            </Part>
          )}

          <GapGroup
            label="Settled"
            note="Answered, assumed or skipped — change any"
            gaps={settled}
            focusId={focusId}
            {...cardProps}
            collapsed
          />
        </Stack>
      </Box>
    </Stack>
  );
}

SetupGapsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  onClose: PropTypes.func,
  focusId: PropTypes.string,
};

/**
 * The blocking ones, inline on Overview — the same cards as the inbox, so
 * they can be answered where they're read.
 */
export function BlockingGapsInline({
  env,
  envState,
  patch,
  onGo,
  skip = [],
  sx,
}) {
  const gaps = sortGaps(setupGaps(env, envState));
  const blocking = gaps.filter(
    (g) => g.status === "blocking" && !skip.includes(g.id),
  );
  if (!blocking.length) return null;
  const meter = gapMeter(gaps);
  const coverage = gapCoverage(env, envState, gaps);
  /* No section header — each row says it blocks the run, and the first
     carries the way to every input. */
  const allInputs = (
    <Button
      size="small"
      onClick={() => openGapsInbox()}
      endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
      sx={{
        color: "text.secondary",
        flexShrink: 0,
        whiteSpace: "nowrap",
        minWidth: 0,
      }}
    >
      {meter.optional
        ? `All inputs · ${meter.optional} optional`
        : "All inputs"}
    </Button>
  );
  return (
    <SectionCard sx={sx}>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {blocking.map((g, i) => (
          <GapCard
            key={g.id}
            gap={g}
            env={env}
            envState={envState}
            patch={patch}
            onGo={onGo}
            coverage={coverage}
            defaultOpen
            inline
            aside={i === 0 ? allInputs : null}
          />
        ))}
      </Stack>
    </SectionCard>
  );
}
BlockingGapsInline.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  skip: PropTypes.array,
  sx: PropTypes.object,
};

/* ── the figures ─────────────────────────────────────────────────────────── */

const num = { fontVariantNumeric: "tabular-nums" };

/* Four counts across the top — red only when something blocks the run. */
function Figures({ meter, coverage }) {
  const cells = [
    {
      k: "Blocking",
      v: meter.blocking,
      sub: meter.blocking ? "Hold the run" : "Run can start",
      red: meter.blocking > 0,
    },
    {
      k: "Optional",
      v: meter.optional,
      sub: meter.optional ? "Scenarios may sit out" : "None open",
    },
    { k: "Resolved", v: meter.resolved, sub: "Settled" },
    /* A count of scenarios, never a coverage percentage. */
    {
      k: "Sit out",
      v: meter.blocking ? "—" : coverage.affected,
      sub: meter.blocking
        ? "Run held"
        : coverage.affected
          ? `of ${coverage.total} scenarios`
          : "Every scenario runs",
    },
  ];
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        "& > * + *": { borderLeft: "1px solid", borderColor: "divider" },
      }}
    >
      {cells.map((c) => (
        <Box key={c.k} sx={{ px: 1.75, py: 1.25, minWidth: 0 }}>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {c.k}
          </Typography>
          <Typography
            sx={{
              typography: "m3",
              fontWeight: 600,
              lineHeight: 1.35,
              mt: 0.25,
              color: c.red ? GAP_STATUS.blocking.color : "text.primary",
              ...num,
            }}
          >
            {c.v}
          </Typography>
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            {c.sub}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
Figures.propTypes = { meter: PropTypes.object, coverage: PropTypes.object };

/* A titled, ruled sheet — the heading reads like the rest of the product's record views. */
function Part({ label, count, note, open = true, onToggle, children }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        {...clickable(onToggle, { expanded: open })}
        sx={{ ...focusRing, mb: 1, ...(onToggle && { cursor: "pointer" }) }}
      >
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            textTransform: "none",
            letterSpacing: 0.5,
            whiteSpace: "nowrap",
            ...num,
          }}
        >
          {label}
          {count != null && (
            <Box
              component="span"
              sx={{
                fontWeight: 600,
                textTransform: "none",
                letterSpacing: 0,
                ml: 0.75,
              }}
            >{`· ${count}`}</Box>
          )}
        </Typography>
        {note && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flex: 1,
              minWidth: 0,
              textAlign: "right",
            }}
          >
            {note}
          </Typography>
        )}
        {onToggle && (
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0, alignSelf: "center" }}
          />
        )}
      </Stack>
      {open && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
Part.propTypes = {
  label: PropTypes.string,
  count: PropTypes.node,
  note: PropTypes.string,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  children: PropTypes.node,
};

/* ── a group of cards ────────────────────────────────────────────────────── */

function GapGroup({
  label,
  note,
  gaps,
  focusId,
  collapsed = false,
  ...cardProps
}) {
  const [open, setOpen] = useState(
    !collapsed || gaps.some((g) => g.id === focusId),
  );
  if (!gaps.length) return null;
  return (
    <Part
      label={label}
      count={gaps.length}
      note={note}
      open={open}
      onToggle={collapsed ? () => setOpen((o) => !o) : undefined}
    >
      {gaps.map((g, i) => (
        <GapCard
          key={g.id}
          gap={g}
          inset={1.75}
          focused={g.id === focusId}
          defaultOpen={
            g.id === focusId ||
            (!focusId &&
              i === 0 &&
              (g.status === "blocking" || g.status === "optional"))
          }
          {...cardProps}
        />
      ))}
    </Part>
  );
}
GapGroup.propTypes = {
  label: PropTypes.string,
  note: PropTypes.string,
  gaps: PropTypes.array,
  focusId: PropTypes.string,
  collapsed: PropTypes.bool,
};

/* ── one gap ─────────────────────────────────────────────────────────────── */

const MONO = "ui-monospace, Menlo, monospace";

const stampLine = (gap) => {
  if (gap.status === "assumed")
    return gap.by === "builder"
      ? `Assumed by the builder — ${gap.answered}`
      : `Assumed — ${gap.answered}`;
  if (gap.status === "confirmed" || gap.status === "resolved")
    return `Confirmed — ${gap.answered || "answered"}`;
  if (gap.status === "skipped") return "Skipped";
  return gap.suggested ? `Suggested — ${gap.suggested}` : null;
};

function GapCard({
  gap,
  env,
  envState,
  patch,
  onGo,
  coverage,
  defaultOpen = false,
  focused = false,
  inset = 2.5,
  inline = false,
  aside = null,
}) {
  const navigate = useNavigate();
  /* Live-test consent is given right here — the same form as the agent's
     Connections tab, saved to the agent — so nobody leaves to agree. */
  const { setAgentValues } = useConnectedAgents();
  const { enqueueSnackbar } = useSnackbar();
  const consentHere =
    gap.code === "gap_live_consent" && !!envState?.agent && !!patch;
  const saveConsent = (values, msg) => {
    setAgentValues(envAgentKey(envState.agent, env?.id), values);
    if (msg) enqueueSnackbar(msg, { variant: "success" });
  };
  const ref = useRef(null);
  const [openState, setOpen] = useState(defaultOpen);
  /* Inline on Overview a blocking row is always open — nothing to fold. */
  const open = inline || openState;
  const [providing, setProviding] = useState(false);
  const [choice, setChoice] = useState(
    gap.answered || gap.suggested || gap.options?.[0] || "",
  );
  const [text, setText] = useState("");
  const meta = GAP_STATUS[gap.status] || GAP_STATUS.optional;
  const waitingOnYou = gap.status === "blocking" || gap.status === "optional";
  const n = gap.coverage?.length || 0;
  const pctOfSuite = coverage?.total
    ? Math.round((n / coverage.total) * 100)
    : 0;

  useEffect(() => {
    if (focused && ref.current)
      ref.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focused]);

  const answer = (stamp, value) => {
    if (!patch) return;
    patch(answerGapPatch(gap, stamp, value, envState));
    setProviding(false);
  };
  const follow = (ask) => {
    if (ask?.href) navigate(ask.href);
    else if (ask?.to) onGo?.(ask.to);
  };

  const tool = gap.scope?.tool;
  const setting =
    gap.code === "gap_prod_risk" && tool
      ? toolSettingOf({ name: tool }, envState)
      : null;
  const line = stampLine(gap);

  return (
    <Box ref={ref} sx={{ ...(focused && { bgcolor: "status.discovery.bg" }) }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        {...clickable(inline ? undefined : () => setOpen((o) => !o), {
          expanded: open,
        })}
        sx={{
          ...focusRing,
          px: inset,
          py: inline ? 1 : 1.25,
          ...(!inline && {
            cursor: "pointer",
            "&:hover": { bgcolor: "action.hover" },
          }),
        }}
      >
        <StatusMark status={gap.status} size={20} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {gap.title}
            {inline && (
              <Box
                component="span"
                sx={{ color: "text.tertiary", fontWeight: 500 }}
              >
                {" · blocks a run"}
              </Box>
            )}
            {inline && consentHere && <InfoTip text={gap.why} />}
          </Typography>
          {/* Inline on Overview the section already says it blocks — no code or status line. */}
          {!inline && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {/* Settled: who settled it and with what, said once. */}
              {!waitingOnYou && line ? line.split(" — ")[0] : meta.label}
              {!open && line && !waitingOnYou && line.includes(" — ")
                ? ` · ${line.split(" — ").slice(1).join(" — ")}`
                : ""}
              {!open && waitingOnYou && n > 0
                ? ` · ${n} scenario${n === 1 ? "" : "s"} rest on it`
                : ""}
            </Typography>
          )}
        </Box>
        {aside}
        {!inline && (
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
        )}
      </Stack>

      <Collapse in={open} unmountOnExit>
        {/* Aligned under the title: why, then the facts as labelled lines, then the ways out. */}
        <Stack
          spacing={1.25}
          sx={{
            pr: inset,
            pb: inline ? 1.25 : 1.75,
            pl: `calc(${inset * 8}px + 32px)`,
          }}
        >
          {/* The consent form says why itself, in one line. */}
          {!consentHere && (
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                lineHeight: 1.6,
              }}
            >
              {gap.why}
            </Typography>
          )}

          {(line ||
            (n > 0 &&
              (gap.status === "optional" || gap.status === "skipped"))) && (
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "92px minmax(0, 1fr)",
                columnGap: 1.5,
                rowGap: 0.5,
              }}
            >
              {line && (
                <>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      lineHeight: 1.6,
                    }}
                  >
                    {line.split(" — ")[0]}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.primary",
                      fontWeight: 600,
                      lineHeight: 1.6,
                    }}
                  >
                    {line.includes(" — ")
                      ? line.split(" — ").slice(1).join(" — ")
                      : "—"}
                  </Typography>
                </>
              )}
              {n > 0 &&
                (gap.status === "optional" || gap.status === "skipped") && (
                  <>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        lineHeight: 1.6,
                      }}
                    >
                      {gap.status === "skipped" ? "Sitting out" : "If skipped"}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        lineHeight: 1.6,
                        ...num,
                      }}
                    >
                      {`${n} ${n === 1 ? (gap.coverageWhat || "scenarios").replace(/^scenarios\b/, "scenario").replace(/\bthat call\b/, "that calls") : gap.coverageWhat || "scenarios"} (${pctOfSuite}% of the suite) ${n === 1 ? "sits" : "sit"} out${gap.status === "skipped" ? "" : " the run"} until this is answered`}
                    </Typography>
                  </>
                )}
            </Box>
          )}

          {/* ── actions ── */}
          {setting && (
            <SafeTargetPrompt
              setting={setting}
              bare
              onConfirm={
                patch
                  ? (url) => patch(safeTargetPatch(envState, tool, url))
                  : null
              }
              onDowngrade={
                patch
                  ? (tier) => patch(downgradeTierPatch(envState, tool, tier))
                  : null
              }
            />
          )}

          {consentHere && (
            <LiveConsentCard
              agent={envState.agent}
              env={env}
              onSave={saveConsent}
              bare
              oneLine={inline}
            />
          )}

          {gap.fixedBy === "action" && !setting && gap.ask && !consentHere && (
            <Box>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => follow(gap.ask)}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              >
                {gap.ask.label}
              </Button>
            </Box>
          )}

          {gap.fixedBy !== "action" && patch && (
            <>
              <Stack
                direction="row"
                spacing={0.75}
                flexWrap="wrap"
                rowGap={0.75}
              >
                {gap.status === "optional" && gap.suggested && (
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    onClick={() => answer("assumed", gap.suggested)}
                  >
                    Accept suggestion
                  </Button>
                )}
                {gap.status === "assumed" && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => answer("confirmed", gap.answered)}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Confirm
                  </Button>
                )}
                {(gap.options?.length > 1 || gap.input) && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => setProviding((p) => !p)}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    {gap.status === "optional" ? "Provide value" : "Change"}
                  </Button>
                )}
                {gap.status === "optional" && (
                  <Button
                    size="small"
                    onClick={() => answer("skipped")}
                    sx={{ color: "text.tertiary" }}
                  >
                    Skip
                  </Button>
                )}
                {gap.by === "you" && (
                  <Button
                    size="small"
                    onClick={() => patch(reopenGapPatch(gap, envState))}
                    sx={{ color: "text.tertiary" }}
                  >
                    {gap.auto ? "Back to the builder's guess" : "Reopen"}
                  </Button>
                )}
                {gap.ask && (
                  <Button
                    size="small"
                    onClick={() => follow(gap.ask)}
                    endIcon={
                      <Iconify icon="solar:arrow-right-linear" width={14} />
                    }
                    sx={{ color: "primary.main" }}
                  >
                    {gap.ask.label}
                  </Button>
                )}
              </Stack>

              <Collapse in={providing} unmountOnExit>
                <Stack spacing={1} sx={{ pt: 0.5 }}>
                  {gap.options?.length > 1 && (
                    <Stack
                      sx={{
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1,
                        overflow: "hidden",
                      }}
                      divider={
                        <Box
                          sx={{
                            borderBottom: "1px solid",
                            borderColor: "divider",
                          }}
                        />
                      }
                    >
                      {gap.options.map((o) => {
                        const sel = !text && choice === o;
                        return (
                          <Stack
                            key={o}
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            role="radio"
                            aria-checked={sel}
                            tabIndex={0}
                            onClick={() => {
                              setChoice(o);
                              setText("");
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                setChoice(o);
                                setText("");
                              }
                            }}
                            sx={{
                              px: 1.25,
                              py: 0.75,
                              cursor: "pointer",
                              bgcolor: sel ? "action.selected" : "transparent",
                              "&:hover": {
                                bgcolor: sel
                                  ? "action.selected"
                                  : "action.hover",
                              },
                            }}
                          >
                            <Iconify
                              icon={
                                sel
                                  ? "solar:check-circle-bold"
                                  : "solar:circle-linear"
                              }
                              width={14}
                              sx={{
                                color: sel ? "text.primary" : "text.disabled",
                                flexShrink: 0,
                              }}
                            />
                            <Typography
                              sx={{
                                typography: "s2",
                                fontWeight: sel ? 600 : 400,
                                flex: 1,
                              }}
                            >
                              {o}
                            </Typography>
                            {o === gap.suggested && (
                              <Typography
                                sx={{
                                  typography: "s3",
                                  color: "text.disabled",
                                }}
                              >
                                default
                              </Typography>
                            )}
                          </Stack>
                        );
                      })}
                    </Stack>
                  )}
                  {gap.input && (
                    <TextField
                      size="small"
                      fullWidth
                      label={gap.input.label}
                      placeholder={gap.input.placeholder}
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                    />
                  )}
                  <Box>
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      disabled={!(text.trim() || choice)}
                      onClick={() => answer("confirmed", text.trim() || choice)}
                    >
                      Save answer
                    </Button>
                  </Box>
                </Stack>
              </Collapse>
            </>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
GapCard.propTypes = {
  gap: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  coverage: PropTypes.object,
  defaultOpen: PropTypes.bool,
  focused: PropTypes.bool,
  inset: PropTypes.number,
  inline: PropTypes.bool,
  aside: PropTypes.node,
};

/*
  Soft tinted circle with a light glyph — red only for blocking; the rest
  share one neutral tone and differ by glyph.
*/
function StatusMark({ status, size = 22 }) {
  const theme = useTheme();
  const colors = statusColors(
    theme,
    status === "blocking" ? "danger" : "neutral",
  );
  const tone = colors.fg;
  const w = Math.round(size * 0.5);
  const common = {
    width: w,
    height: w,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: tone,
    strokeWidth: 2.5,
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };
  const glyph = (() => {
    if (status === "confirmed" || status === "resolved")
      return <polyline points="5,12.5 10,17.5 19,7" />;
    if (status === "blocking")
      return (
        <>
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="18" y1="6" x2="6" y2="18" />
        </>
      );
    if (status === "skipped") return <line x1="7" y1="12" x2="17" y2="12" />;
    if (status === "assumed")
      return (
        <>
          <circle cx="8" cy="12" r="1.3" fill={tone} stroke="none" />
          <circle cx="12" cy="12" r="1.3" fill={tone} stroke="none" />
          <circle cx="16" cy="12" r="1.3" fill={tone} stroke="none" />
        </>
      );
    return (
      <>
        <circle cx="12" cy="7.5" r="1.5" fill={tone} stroke="none" />
        <line x1="12" y1="11" x2="12" y2="17" />
      </>
    );
  })();
  return (
    <Box
      role="img"
      aria-label={(GAP_STATUS[status] || GAP_STATUS.optional).label}
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        bgcolor: colors.bg,
        border: "1px solid",
        borderColor: colors.border,
      }}
    >
      <Box component="svg" {...common} sx={{ display: "block" }}>
        {glyph}
      </Box>
    </Box>
  );
}
StatusMark.propTypes = { status: PropTypes.string, size: PropTypes.number };

/* ── credentials, as a manifest ──────────────────────────────────────────── */

/*
  Reading the source says which secrets are needed and where each
  requirement came from, so the list is stated rather than asked. Values
  never appear here: a requirement carries a reference into the team's
  secret manager (AC-6.12), and "Set reference" accepts only one. Setting
  it re-runs the access check and nothing else.
  Neutral throughout — a missing credential doesn't hold the run on its own.
*/
function CredentialsList({ credentials, envState, patch }) {
  const [editing, setEditing] = useState(null);
  if (!credentials.length) return null;
  /* A moved reference is the environment's next (credentials-only) version. */
  const setRef = (c, ref) => {
    patch?.(
      credentialSetPatch(
        envState,
        credentialRefPatch(envState, c.id, ref, c.provider),
      ),
    );
    setEditing(null);
  };
  return (
    <>
      {credentials.map((c) => {
        const open = editing === c.id;
        const managed = !!c.satisfiedBy;
        return (
          <Box key={c.id} sx={{ px: 1.75, py: 1.125 }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) auto",
                columnGap: 2,
                alignItems: "start",
              }}
            >
              <Box minWidth={0}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: MONO,
                    fontSize: 13,
                  }}
                >
                  {c.id}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >{`${c.purpose} · from ${c.detectedFrom}`}</Typography>
              </Box>
              {patch && !managed && !open && (
                <Box sx={{ pt: "2px" }}>
                  <SetRefAction
                    onClick={() => setEditing(c.id)}
                    label={c.ref ? "Change" : "Set reference"}
                  />
                </Box>
              )}
            </Box>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.5, minWidth: 0 }}
            >
              {managed ? (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.secondary" }}
                >{`Using ${c.satisfiedBy}`}</Typography>
              ) : c.ref ? (
                <>
                  <SecretRefText value={c.ref} sx={{ typography: "s3" }} />
                  {c.migrated && <RefTag>moved from a pasted key</RefTag>}
                </>
              ) : (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {c.status === "configured"
                    ? "Held by the agent connection · no reference recorded"
                    : c.status === "missing"
                      ? "Not set"
                      : "Optional · not set"}
                </Typography>
              )}
            </Stack>
            {open && (
              <Box sx={{ pt: 1 }}>
                <SecretRefEditor
                  placeholder={c.ref || `vault://acme/sim/${c.id}`}
                  onSave={(ref) => setRef(c, ref)}
                  onCancel={() => setEditing(null)}
                />
              </Box>
            )}
            {!open && c.check && (
              <PreflightLine check={c.check} sx={{ mt: 0.5 }} />
            )}
          </Box>
        );
      })}
    </>
  );
}
CredentialsList.propTypes = {
  credentials: PropTypes.array,
  envState: PropTypes.object,
  patch: PropTypes.func,
};
