import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  agentVersions,
  currentAgentVersion,
  defaultAgentVersionOf,
  productionAgentVersion,
} from "../_mock/versions";
import { versionHistoryOf } from "../_mock/nudgeEffects";
import {
  SNAPSHOT_KIND_LABEL,
  snapshotHistoryOf,
  snapshotLabelOf,
} from "../_mock/envSnapshot";
import { envVersionNameOf } from "../_mock/codeRefs";
import { simulatorOf } from "../_mock/envModel";
import { runSummaries } from "../_mock/comparison";
import { runStatsOf } from "../_mock/runCompare";
import { contractWordingIn } from "../_mock/rlContract";
import { ago } from "../agents/agentViewUtils";
import { segmentedToggleSx } from "../agents/segmentedToggleSx";
import { clickable, focusRing } from "../components/clickable";
import useEnvViewAs from "./useEnvViewAs";
import RollbackDialog from "./RollbackDialog";
import { ContractWording } from "./VersionHistory";

const MONO = "ui-monospace, Menlo, monospace";
const PART_NAME = {
  env: "World and contract",
  set: "Scenarios",
  catalog: "Sub-goals",
  eval: "Evaluations",
  caller: "Simulated caller",
  creds: "Credentials",
};
const KIND_ICON = {
  env: "solar:layers-minimalistic-linear",
  agent: "solar:cpu-bolt-linear",
  default: "solar:flag-linear",
};
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const dayOf = (iso) => {
  if (!iso) return "Earlier";
  const d = new Date(iso);
  const today = new Date();
  const diff = Math.floor(
    (new Date(today.toDateString()) - new Date(d.toDateString())) / 864e5,
  );
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
};
/* A record's internal note, in words a person reads. */
const plainNote = (text) =>
  /^Moved from Simulate/i.test(String(text || ""))
    ? "The version this environment was built with."
    : text;

/**
 * The environment's History tab (its own page — the version bar keeps the
 * compact menu): what is tested now, then one timeline of every environment
 * version, agent version and default change, newest first, grouped by day.
 * Each entry says what changed, how its runs went, and holds its actions —
 * View, Roll back, Make a copy — in its menu.
 */
export default function HistoryPage({
  env,
  envState,
  patch,
  readOnly,
  onCopy,
}) {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);
  const [menu, setMenu] = useState(null);
  const [rollbackTo, setRollbackTo] = useState(null);
  const { viewAs, setViewAs } = useEnvViewAs(env, envState);
  const canSwitch = !readOnly && !!patch && !viewAs;

  const snapshots = useMemo(
    () => snapshotHistoryOf(env, envState),
    [env, envState],
  );
  const runs = useMemo(
    () =>
      runSummaries(env, envState).filter(
        (r) => r.finishedAt && !r.synthetic && r.kind !== "trial",
      ),
    [env, envState],
  );
  /* The latest finished run on a version: its pass rate. */
  const rateOf = (pick) => {
    const on = runs
      .filter(pick)
      .sort((a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt));
    if (!on.length) return null;
    const s = runStatsOf(on[0]);
    return {
      rate: s.scenarioRate,
      passed: s.scenarioPassed,
      of: s.measured,
      runs: on.length,
    };
  };
  const nm = (l) =>
    l === "production" ? l : envVersionNameOf(envState, l, env?.id).main;
  const activeAgent = currentAgentVersion(envState)?.label;
  const production = productionAgentVersion(envState)?.label;
  const def = defaultAgentVersionOf(envState);
  const agentRecords = new Map(
    agentVersions(envState).map((v) => [v.label, v]),
  );
  const current = snapshots.find((v) => v.current) || snapshots[0];

  const events = useMemo(() => {
    const envEvents = snapshots.map((v) => ({
      kind: "env",
      key: `env-${v.label}`,
      at: v.at,
      v,
      rate: rateOf((r) => snapshotLabelOf(r, env, envState) === v.label),
    }));
    const agentEvents = versionHistoryOf(env, envState).agent.map((row) => ({
      kind: "agent",
      key: `agent-${row.label}`,
      at: row.at,
      row,
      record: agentRecords.get(row.label),
      rate: rateOf((r) => (r.agentVersion || "v1") === row.label),
    }));
    const defaults = (envState?.agentDefaultChanges || []).map((c) => ({
      kind: "default",
      key: `default-${c.at}`,
      at: c.at,
      c,
    }));
    return [...envEvents, ...agentEvents, ...defaults].sort(
      (a, b) => (Date.parse(b.at || "") || 0) - (Date.parse(a.at || "") || 0),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshots, env, envState, runs]);
  const shown = events.filter(
    (e) =>
      filter === "all" ||
      (filter === "env" ? e.kind === "env" : e.kind !== "env"),
  );
  const days = [];
  shown.forEach((e) => {
    const d = dayOf(e.at);
    const last = days[days.length - 1];
    if (last && last.day === d) last.items.push(e);
    else days.push({ day: d, items: [e] });
  });

  const nowRate = rateOf(
    (r) =>
      snapshotLabelOf(r, env, envState) === current?.label &&
      (r.agentVersion || "v1") === activeAgent,
  );
  const agentCount = versionHistoryOf(env, envState).agent.length;

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      {/* What runs use now, in one strip. */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "repeat(2, 1fr)", md: "repeat(4, 1fr)" },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          overflow: "hidden",
        }}
      >
        {[
          {
            label: "Environment",
            value: current?.label || "—",
            sub: current?.latest
              ? "latest · runs use this"
              : "runs use this · a newer one exists",
          },
          {
            label: "Agent being tested",
            value: activeAgent ? nm(activeAgent) : "—",
            sub:
              def === "production"
                ? `follows production (${nm(production)})`
                : `pinned · ${nm(production)} is in production`,
          },
          {
            label: "Versions",
            value: snapshots.length + agentCount,
            sub: `${plural(snapshots.length, "environment version")} · ${plural(agentCount, "agent version")}`,
          },
          {
            label: "This setup's latest run",
            value: nowRate?.rate != null ? `${nowRate.rate}%` : "—",
            sub: nowRate
              ? `${nowRate.passed} of ${nowRate.of} passed · ${plural(nowRate.runs, "run")}`
              : "not run on this setup yet",
          },
        ].map((m, i) => (
          <Box
            key={m.label}
            sx={{
              px: 2,
              py: 1.5,
              borderLeft: i ? "1px solid" : "none",
              borderColor: "divider",
              minWidth: 0,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", fontWeight: 500 }}
            >
              {m.label}
            </Typography>
            <Typography
              sx={{
                fontSize: 22,
                fontWeight: 600,
                lineHeight: 1.2,
                mt: 0.5,
                fontFamily: /^v\d|@/.test(String(m.value)) ? MONO : undefined,
              }}
            >
              {m.value}
            </Typography>
            <Typography
              noWrap
              title={m.sub}
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {m.sub}
            </Typography>
          </Box>
        ))}
      </Box>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: {
            xs: "1fr",
            lg: "minmax(0, 2fr) minmax(0, 1fr)",
          },
          alignItems: "start",
        }}
      >
        {/* ── the timeline ── */}
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, pt: 2, pb: 1.5 }}
          >
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{ typography: "s1", fontWeight: 600, fontSize: 13 }}
              >
                Timeline
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
              >
                Any change to the world, scenarios, evaluations, caller or
                credentials is the next environment version. An agent change
                never is.
              </Typography>
            </Box>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={filter}
              onChange={(_, v) => v && setFilter(v)}
              sx={segmentedToggleSx}
            >
              <ToggleButton value="all">All</ToggleButton>
              <ToggleButton value="env">Environment</ToggleButton>
              <ToggleButton value="agent">Agent</ToggleButton>
            </ToggleButtonGroup>
          </Stack>
          {days.map((d) => (
            <Box key={d.day}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  px: 2.5,
                  py: 0.75,
                  borderTop: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                }}
              >
                {d.day}
              </Typography>
              {d.items.map((e) => (
                <Entry
                  key={e.key}
                  e={e}
                  nm={nm}
                  expanded={open === e.key}
                  onToggle={() => setOpen(open === e.key ? null : e.key)}
                  onMenu={(el) => setMenu({ el, e })}
                  activeAgent={activeAgent}
                  viewAs={viewAs}
                  env={env}
                  envState={envState}
                />
              ))}
            </Box>
          ))}
          {!days.length && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary", px: 2.5, pb: 2 }}
            >
              Nothing here yet.
            </Typography>
          )}
        </Box>

        {/* ── what's tested now ── */}
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <Typography
            sx={{
              typography: "s1",
              fontWeight: 600,
              fontSize: 13,
              px: 2.5,
              pt: 2,
              pb: 1,
            }}
          >
            What runs use now
          </Typography>
          {[
            [
              "Environment",
              `${current?.label || "—"}${current?.latest ? " · latest" : ""}`,
            ],
            [
              "World and contract",
              current?.parts?.env ? `world ${current.parts.env}` : "—",
            ],
            [
              "Scenarios",
              plural(
                (envState?.scenarios || []).filter(
                  (s) => !s.archived && !s.retired && !s.proposed,
                ).length,
                "scenario",
              ),
            ],
            [
              "Evaluations",
              plural((envState?.evals || []).length, "evaluation"),
            ],
            ["Simulated caller", simulatorOf(env, envState)?.model || "—"],
            [
              "Agent",
              activeAgent
                ? `${nm(activeAgent)}${def === "production" ? " · follows production" : " · pinned"}`
                : "None connected",
            ],
          ].map(([k, v]) => (
            <Stack
              key={k}
              direction="row"
              spacing={1.5}
              sx={{
                px: 2.5,
                py: 0.875,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 128,
                  flexShrink: 0,
                }}
              >
                {k}
              </Typography>
              <Typography
                sx={{ typography: "s2", minWidth: 0 }}
                noWrap
                title={v}
              >
                {v}
              </Typography>
            </Stack>
          ))}
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              px: 2.5,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            Roll back makes the next version from an older one. Nothing is
            deleted, and every run keeps the version it ran on.
          </Typography>
        </Box>
      </Box>

      {/* An entry's actions. */}
      <Menu anchorEl={menu?.el} open={!!menu} onClose={() => setMenu(null)}>
        {menu?.e.kind === "env" &&
          menu.e.v.worldHead &&
          !menu.e.v.current &&
          menu.e.v.parts.env !== viewAs && (
            <MenuItem
              onClick={() => {
                setViewAs(menu.e.v.parts.env);
                setMenu(null);
              }}
            >
              View it, read-only
            </MenuItem>
          )}
        {menu?.e.kind === "env" && !menu.e.v.current && canSwitch && (
          <MenuItem
            onClick={() => {
              setRollbackTo(menu.e.v.label);
              setMenu(null);
            }}
          >
            {`Roll back to ${menu.e.v.label}`}
          </MenuItem>
        )}
        {menu?.e.kind === "env" && onCopy && (
          <MenuItem
            onClick={() => {
              onCopy(menu.e.v.label);
              setMenu(null);
            }}
          >
            {`Make a copy from ${menu.e.v.label}`}
          </MenuItem>
        )}
        {menu?.e.kind === "env" && (
          <MenuItem
            onClick={() => {
              setOpen(menu.e.key);
              setMenu(null);
            }}
          >
            See what changed
          </MenuItem>
        )}
      </Menu>
      {patch && (
        <RollbackDialog
          open={!!rollbackTo}
          targetLabel={rollbackTo}
          env={env}
          envState={envState}
          patch={patch}
          onClose={() => setRollbackTo(null)}
        />
      )}
    </Stack>
  );
}

HistoryPage.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
  onCopy: PropTypes.func,
};

/* One timeline entry: what it is, what changed, how its runs went. */
function Entry({
  e,
  nm,
  expanded,
  onToggle,
  onMenu,
  activeAgent,
  viewAs,
  env,
  envState,
}) {
  const isEnv = e.kind === "env";
  const title =
    e.kind === "env"
      ? `Environment ${e.v.label}`
      : e.kind === "agent"
        ? `Agent ${nm(e.row.label)}`
        : "Default agent changed";
  const badges = [
    isEnv && e.v.current && "runs use this",
    isEnv && e.v.latest && !e.v.current && "latest",
    isEnv && viewAs && e.v.worldHead && e.v.parts.env === viewAs && "viewing",
    e.kind === "agent" && e.row.label === activeAgent && "being tested",
    e.kind === "agent" && e.record?.status === "draft" && "draft",
  ].filter(Boolean);
  const summary =
    e.kind === "env"
      ? e.v.summary
      : e.kind === "agent"
        ? plainNote(e.row.summary)
        : `From ${nm(e.c.from === "production" ? "production" : e.c.from)} to ${nm(e.c.to === "production" ? "production" : e.c.to)}`;
  const parts = isEnv
    ? e.v.kinds.map((k) => SNAPSHOT_KIND_LABEL[k]).filter(Boolean)
    : [];
  const canOpen = isEnv && e.v.details?.length;

  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="flex-start"
        {...(canOpen ? clickable(onToggle, { expanded }) : {})}
        sx={{
          ...focusRing,
          px: 2.5,
          py: 1.25,
          cursor: canOpen ? "pointer" : "default",
          "&:hover": canOpen ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "divider",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            color: "text.secondary",
          }}
        >
          <Iconify icon={KIND_ICON[e.kind]} width={15} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="center"
            flexWrap="wrap"
            rowGap={0.25}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {title}
            </Typography>
            {badges.map((b) => (
              <Typography
                key={b}
                sx={{
                  typography: "s3",
                  color:
                    b === "runs use this" || b === "being tested"
                      ? "status.discovery.fg"
                      : "text.tertiary",
                }}
              >
                {`· ${b}`}
              </Typography>
            ))}
          </Stack>
          {summary && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
            >
              {summary}
            </Typography>
          )}
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            {[
              parts.length ? `Changed: ${parts.join(", ")}` : null,
              e.kind === "default" && (e.c.by || "You"),
              e.at && ago(e.at),
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
        {/* How its runs went — the latest run on it. */}
        {e.kind !== "default" && (
          <Box sx={{ width: 132, flexShrink: 0, textAlign: "right" }}>
            {e.rate ? (
              <>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >{`${e.rate.rate}%`}</Typography>
                <Box
                  sx={{
                    mt: 0.5,
                    height: 4,
                    borderRadius: 2,
                    bgcolor: "action.hover",
                    overflow: "hidden",
                  }}
                >
                  <Box
                    sx={{
                      width: `${e.rate.rate}%`,
                      height: "100%",
                      bgcolor: "text.secondary",
                    }}
                  />
                </Box>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    mt: 0.25,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`${e.rate.passed}/${e.rate.of} · ${plural(e.rate.runs, "run")}`}
                </Typography>
              </>
            ) : (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                No runs on it
              </Typography>
            )}
          </Box>
        )}
        {isEnv ? (
          <IconButton
            size="small"
            aria-label={`Actions for ${title}`}
            onClick={(ev) => {
              ev.stopPropagation();
              onMenu(ev.currentTarget);
            }}
          >
            <Iconify icon="solar:menu-dots-bold" width={16} />
          </IconButton>
        ) : (
          <Box sx={{ width: 26, flexShrink: 0 }} />
        )}
      </Stack>
      {expanded && canOpen && (
        <Box sx={{ pl: 8.5, pr: 2.5, pb: 1.5 }}>
          {e.v.details.map((d, i) => (
            <Box key={`${d.part}-${d.label}-${i}`} sx={{ mt: 0.5 }}>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                <Box
                  component="span"
                  sx={{ color: "text.secondary", fontWeight: 600 }}
                >
                  {PART_NAME[d.part] || d.part}
                </Box>
                {d.text ? ` — ${d.text}` : ""}
              </Typography>
              {d.part === "env" && (
                <ContractWording
                  items={contractWordingIn(env, envState, d.label)}
                />
              )}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
}
Entry.propTypes = {
  e: PropTypes.object.isRequired,
  nm: PropTypes.func.isRequired,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func,
  onMenu: PropTypes.func,
  activeAgent: PropTypes.string,
  viewAs: PropTypes.string,
  env: PropTypes.object,
  envState: PropTypes.object,
};
