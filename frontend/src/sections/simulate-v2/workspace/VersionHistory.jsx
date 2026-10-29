import PropTypes from "prop-types";
import { Children, useState } from "react";
import { Box, Button, Menu, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  agentVersions,
  currentAgentVersion,
  defaultAgentVersionOf,
  isCredentialsVersion,
  productionAgentVersion,
} from "../_mock/versions";
import { releasedNote } from "../agents/versionRelease";
import { useOptionalSimStore } from "../store";
import { versionHistoryOf } from "../_mock/nudgeEffects";
import { snapshotHistoryOf, SNAPSHOT_KIND_LABEL } from "../_mock/envSnapshot";
import { envVersionNameOf } from "../_mock/codeRefs";
import { ago } from "../agents/agentViewUtils";
import useEnvViewAs from "./useEnvViewAs";
import { clickable, focusRing } from "../components/clickable";
import RollbackDialog from "./RollbackDialog";
import { contractWordingIn } from "../_mock/rlContract";

const MONO = "ui-monospace, Menlo, monospace";
const runs = (n) => `${n} run${n === 1 ? "" : "s"}`;
const PART_NAME = {
  env: "World and contract",
  set: "Scenarios",
  catalog: "Sub-goals",
  eval: "Evaluations",
  caller: "Simulated caller",
  creds: "Credentials",
};
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const defaultName = (d) => (d === "production" ? "production" : d);

/* What the harness found when a version reached this environment. */
const checkLine = (v, check) => {
  /* Its own note already says it behaves as its base. */
  if (isCredentialsVersion(v)) return null;
  if (!check) return null;
  const untested = check.untested
    ? ` · ${plural(check.untested, "new rule")} nothing tests yet`
    : "";
  if (check.result === "none")
    return `Checked here · no changes needed${untested}`;
  return `Checked here · ${[check.tools && plural(check.tools, "tool change"), check.rules && plural(check.rules, "rule change")].filter(Boolean).join(" and ") || "changes"} to review${untested}`;
};

/**
 * Version history: one list of environment versions, newest first.
 *
 * An environment version is a snapshot of everything that decides what is
 * tested and how it is judged — the world and its contract, the scenario set,
 * the sub-goal catalogue, the evaluations and the simulated caller. Any change
 * to any of them is the next version; an agent change never is (envSnapshot.js).
 * Each row reads "v7 · 3 scenarios added from ISS-4529 · 2h ago" and opens to
 * what changed in it, in words.
 *
 * "Roll back" makes the next version with everything as in an older one —
 * world, scenarios, sub-goals, evaluations and caller (RollbackDialog says
 * what moves first) — and is offered on every older version. "View" opens a
 * world read-only, so it's offered on the newest version of each world.
 */
export default function VersionHistoryMenu({
  anchor,
  onClose = () => {},
  env,
  envState,
  patch,
  readOnly = false,
  /* The environment's History tab: the same history as a page, not a menu. */
  page = false,
  /* Make a copy from this version (the dialog opens with it preselected). */
  onCopy = null,
}) {
  const snapshots = snapshotHistoryOf(env, envState);
  /* A released improvement's note drops its "not live until released" tail —
     the agent's Versions tab reads it the same way. Released anywhere the
     agent is tested counts, as Agents reads it. */
  const store = useOptionalSimStore();
  const records = new Map(agentVersions(envState).map((v) => [v.label, v]));
  const releasedHere = new Set(
    [envState, ...Object.values(store?.state?.byEnv || {})].flatMap((es) =>
      (es?.releases || []).map((r) => r.version),
    ),
  );
  const agents = versionHistoryOf(env, envState).agent.map((row) => {
    const v = records.get(row.label);
    return v?.note
      ? {
          ...row,
          summary: releasedNote(
            v,
            v.status !== "draft" || releasedHere.has(v.label),
          ),
        }
      : row;
  });
  const activeAgent = currentAgentVersion(envState)?.label;
  /* The agent side of history: each version with what the check found here,
     and every change of this environment's default — newest first. */
  const versionsByLabel = new Map(
    agentVersions(envState).map((v) => [v.label, v]),
  );
  const checks = new Map(
    (envState?.agentChecks || []).map((c) => [c.version, c]),
  );
  const production = productionAgentVersion(envState)?.label;
  const def = defaultAgentVersionOf(envState);
  /* A code agent's versions read as commits. */
  const nm = (l) =>
    l === "production" ? l : envVersionNameOf(envState, l, env?.id).main;
  const agentRows = [
    ...agents.map((row) => ({
      type: "version",
      at: row.at,
      row,
      check: checkLine(versionsByLabel.get(row.label), checks.get(row.label)),
    })),
    ...(envState?.agentDefaultChanges || []).map((c) => ({
      type: "default",
      at: c.at,
      c,
    })),
  ].sort(
    (a, b) => (Date.parse(b.at || "") || 0) - (Date.parse(a.at || "") || 0),
  );
  const { viewAs, setViewAs } = useEnvViewAs(env, envState);
  const [open, setOpen] = useState(null);
  const [rollbackTo, setRollbackTo] = useState(null);
  const canSwitch = !readOnly && !!patch && !viewAs;
  /* An environment left on an older world by an earlier pointer switch:
     the latest is one click back, as itself — not a rollback. */
  const onOlder =
    snapshots.some((v) => v.latest && !v.current) &&
    snapshots.find((v) => v.current)?.parts.env !==
      snapshots.find((v) => v.latest)?.parts.env;

  const body = (
    <>
      {/* On the History tab the tab names the page. */}
      {!page && (
        <Box sx={{ px: 1.25, pt: 0.75, pb: 1 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.5,
            }}
          >
            Version history
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            Any change to the world, scenarios, evaluations, caller or
            credentials is the next version. An agent change never is.
          </Typography>
        </Box>
      )}

      {/* ── environment ── */}
      <Section
        title="Environment"
        note={`${snapshots.length} version${snapshots.length === 1 ? "" : "s"}`}
      />
      <Stack spacing={0.25}>
        {snapshots.map((v) => {
          const isActive = v.current;
          const isViewed = v.worldHead && v.parts.env === viewAs;
          const expanded = open === v.label;
          const offer = v.worldHead && !isActive;
          /* "Use latest" puts the pointer back on the newest world; every other
             older version can be rolled back to. */
          const useLatest = v.latest && onOlder;
          return (
            <Box
              key={v.label}
              sx={{
                borderRadius: 1,
                border: "1px solid",
                borderColor: isActive
                  ? "status.discovery.border"
                  : "transparent",
                bgcolor: isActive ? "status.discovery.bg" : "transparent",
              }}
            >
              <Stack
                direction="row"
                spacing={1.25}
                alignItems="flex-start"
                {...clickable(() => setOpen(expanded ? null : v.label), {
                  expanded,
                })}
                sx={{
                  ...focusRing,
                  px: 1.25,
                  py: 1,
                  cursor: "pointer",
                  borderRadius: 1,
                  "&:hover": {
                    bgcolor: isActive ? undefined : "action.hover",
                  },
                }}
              >
                <Iconify
                  icon={
                    isActive ? "solar:check-circle-bold" : "solar:circle-linear"
                  }
                  width={16}
                  sx={{
                    color: isActive ? "primary.main" : "text.disabled",
                    flexShrink: 0,
                    mt: "1px",
                  }}
                />
                <Box flex={1} minWidth={0}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.625}
                    flexWrap="wrap"
                    rowGap={0.375}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: 600,
                        fontFamily: MONO,
                      }}
                    >
                      {v.label}
                    </Typography>
                    {v.latest && <Badge label="latest" />}
                    {isActive && <Badge label="active" tone="discovery" />}
                    {isViewed && <Badge label="viewing" />}
                    <Box flex={1} />
                    {/* Roll back makes the next version from this one's world and
                        contract — the dialog says what moves before it does. */}
                    {!isActive && canSwitch && (
                      <Tooltip
                        arrow
                        title={
                          useLatest
                            ? "Runs use the latest world again."
                            : `Make the next version with everything as in ${v.label} — world, scenarios, sub-goals, evaluations and caller. Nothing is deleted.`
                        }
                      >
                        <Button
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (useLatest)
                              patch({ activeEnvVersion: v.parts.env });
                            else setRollbackTo(v.label);
                            onClose();
                          }}
                          sx={{ color: "text.secondary", minWidth: 0 }}
                        >
                          {useLatest ? "Use latest" : "Roll back"}
                        </Button>
                      </Tooltip>
                    )}
                    {onCopy && (
                      <Button
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          onCopy(v.label);
                          onClose();
                        }}
                        sx={{ color: "text.secondary", minWidth: 0 }}
                      >
                        Make a copy
                      </Button>
                    )}
                    {offer && !isViewed && (
                      <Button
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          setViewAs(v.parts.env);
                          onClose();
                        }}
                        sx={{ color: "text.secondary", minWidth: 0 }}
                      >
                        View
                      </Button>
                    )}
                    <Iconify
                      icon={
                        expanded
                          ? "solar:alt-arrow-up-linear"
                          : "solar:alt-arrow-down-linear"
                      }
                      width={12}
                      sx={{ color: "text.disabled" }}
                    />
                  </Stack>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      mt: 0.25,
                    }}
                  >
                    {v.summary}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      mt: 0.25,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {[
                      v.kinds.map((k) => SNAPSHOT_KIND_LABEL[k]).join(" + "),
                      runs(v.runs),
                      v.at && ago(v.at),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                </Box>
              </Stack>
              {expanded && (
                <Box sx={{ pl: 4.75, pr: 1.25, pb: 1 }}>
                  {v.details.map((d, i) => (
                    <Box key={`${d.part}-${d.label}-${i}`}>
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          mt: 0.25,
                        }}
                      >
                        <Box
                          component="span"
                          sx={{ color: "text.secondary", fontWeight: 600 }}
                        >
                          {PART_NAME[d.part] || d.part}
                        </Box>
                        {d.text ? ` — ${d.text}` : ""}
                      </Typography>
                      {/* The words that changed, not just a count of them. */}
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
        })}
      </Stack>

      {/* ── agent ── */}
      <Section
        title="Agent"
        note={
          agents.length
            ? def === "production"
              ? `Default: production (${nm(production)})`
              : `Default: ${nm(def)} · ${nm(production)} is in production`
            : "The test subject — every change is a new version"
        }
      />
      {agents.length ? (
        agentRows.map((r) =>
          r.type === "default" ? (
            <Line
              key={`default-${r.c.at}`}
              label="Default"
              summary={`Changed from ${nm(defaultName(r.c.from))} to ${nm(defaultName(r.c.to))}${r.c.production && r.c.to !== "production" ? ` · ${nm(r.c.production)} was in production` : ""}`}
              meta={`${r.c.by || "You"} · ${ago(r.c.at)}`}
            />
          ) : (
            <Line
              key={r.row.label}
              label={`Agent ${nm(r.row.label)}`}
              badges={[
                r.row.label === activeAgent && {
                  label: "testing",
                  tone: "discovery",
                },
              ].filter(Boolean)}
              summary={
                r.check
                  ? `${String(r.row.summary || "").replace(/\.?\s*$/, ".")} ${r.check}.`
                  : r.row.summary
              }
              meta={`${runs(r.row.runs)} · ${ago(r.row.at)}`}
            />
          ),
        )
      ) : (
        <Empty text="No agent connected yet." />
      )}
    </>
  );

  return (
    <>
      {page ? (
        <Box sx={{ p: 2, maxWidth: 760 }}>{body}</Box>
      ) : (
        <Menu
          anchorEl={anchor}
          open={!!anchor}
          onClose={onClose}
          anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
          transformOrigin={{ vertical: "top", horizontal: "left" }}
          slotProps={{
            paper: {
              sx: {
                width: 460,
                maxWidth: "calc(100vw - 32px)",
                maxHeight: "72vh",
                mt: 0.5,
                p: 0.75,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: "divider",
              },
            },
            list: { sx: { p: 0 } },
          }}
        >
          {Children.toArray(body.props.children)}
        </Menu>
      )}
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
    </>
  );
}

VersionHistoryMenu.propTypes = {
  onCopy: PropTypes.func,
  anchor: PropTypes.object,
  onClose: PropTypes.func,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
  page: PropTypes.bool,
};

const WORDING_TONE = { added: "status.success.fg" };

/* Each changed rule, use case or argument as it read before and after — a
   removed line struck through, an added one in green. */
export function ContractWording({ items }) {
  if (!items.length) return null;
  return (
    <Stack spacing={0.75} sx={{ mt: 0.75, mb: 0.25 }}>
      {items.map((c) => (
        <Box key={`${c.what}-${c.kind}`}>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
          >
            {`${c.what} ${c.kind}`}
          </Typography>
          {c.from && (
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO,
                fontSize: 12,
                color: "text.disabled",
                textDecoration: "line-through",
                mt: 0.25,
              }}
            >
              {c.from}
            </Typography>
          )}
          {c.to && (
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO,
                fontSize: 12,
                color: c.kind === "added" ? WORDING_TONE.added : "text.primary",
                mt: 0.25,
              }}
            >
              {c.to}
            </Typography>
          )}
        </Box>
      ))}
    </Stack>
  );
}
ContractWording.propTypes = { items: PropTypes.array.isRequired };

function Section({ title, note }) {
  return (
    <Stack
      direction="row"
      alignItems="baseline"
      spacing={0.75}
      sx={{
        px: 1.25,
        pt: 1.25,
        pb: 0.5,
        mt: 0.5,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
      >
        {title}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
      >
        {note}
      </Typography>
    </Stack>
  );
}
Section.propTypes = { title: PropTypes.string, note: PropTypes.string };

function Line({ label, badges = [], summary, meta }) {
  return (
    <Box sx={{ px: 1.25, py: 0.75 }}>
      <Stack direction="row" alignItems="center" spacing={0.625}>
        {label && (
          <Typography
            sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}
          >
            {label}
          </Typography>
        )}
        {badges.map((b) => (
          <Badge key={b.label} label={b.label} tone={b.tone} />
        ))}
        <Box flex={1} />
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {meta}
        </Typography>
      </Stack>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", mt: label ? 0.25 : 0 }}
      >
        {summary}
      </Typography>
    </Box>
  );
}
Line.propTypes = {
  label: PropTypes.string,
  badges: PropTypes.array,
  summary: PropTypes.string,
  meta: PropTypes.string,
};

function Empty({ text }) {
  return (
    <Typography
      sx={{ px: 1.25, py: 0.75, typography: "s3", color: "text.tertiary" }}
    >
      {text}
    </Typography>
  );
}
Empty.propTypes = { text: PropTypes.string };

/* A qualifier in a list row — plain text, tinted when a tone (status role) is given. */
function Badge({ label, tone }) {
  return (
    <Typography
      component="span"
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: tone ? `status.${tone}.fg` : "text.tertiary",
      }}
    >
      {label}
    </Typography>
  );
}
Badge.propTypes = { label: PropTypes.string, tone: PropTypes.string };
