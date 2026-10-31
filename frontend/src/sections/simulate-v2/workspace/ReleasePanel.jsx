import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CodeBlock from "src/components/code-block";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { SectionCard } from "../components/primitives";
import {
  RELEASE_CHECKS,
  checksStateOf,
  lockPatch,
  rlCodeOf,
  rlKeyPatch,
  runChecksPatch,
} from "../_mock/release";

/*
  An environment release (catalogue.md, rl.md): five release checks gate
  Ready — it starts, it resets, the reference passes, a wrong answer fails,
  hidden checks don't leak. Until all five pass on this version it's Checks
  pending; an edit makes a new version, so the checks run again. A ready
  release can be locked: tasks, train/test splits and the scorer freeze so
  scores stay comparable, and only a locked release opens RL access.
*/
export default function ReleasePanel({ env, envState, patch, onCopy }) {
  const st = checksStateOf(env, envState);
  const locked = envState?.lockedRelease || null;
  const [running, setRunning] = useState(false);
  const [confirmLock, setConfirmLock] = useState(false);
  const [split, setSplit] = useState("train");
  useEffect(() => {
    if (!running) return undefined;
    const t = setTimeout(() => {
      patch?.(runChecksPatch(env, envState));
      setRunning(false);
    }, 1600);
    return () => clearTimeout(t);
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const status = locked
    ? { label: "Locked", role: "neutral" }
    : st.ready
      ? { label: "Ready", role: "success" }
      : { label: "Checks pending", role: "warning" };

  const cell = { typography: "s2", py: 1, px: 1.5 };
  const head = {
    typography: "s3",
    color: "text.tertiary",
    py: 0.75,
    px: 1.5,
    fontWeight: 500,
  };

  return (
    <SectionCard
      title={`Release ${locked?.version || st.version}`}
      subtitle={
        locked
          ? "Frozen so scores stay comparable. Copy it to edit."
          : "Release checks decide when this environment is Ready."
      }
      action={
        <StatusBadge
          status={status.label}
          label={status.label}
          role={status.role}
        />
      }
      sx={{ mb: 3 }}
    >
      {/* ── the five release checks ── */}
      <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
        <Box component="thead">
          <Box component="tr" sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Box component="th" sx={{ ...head, textAlign: "left", width: 180 }}>
              Check
            </Box>
            <Box component="th" sx={{ ...head, textAlign: "left" }}>
              What it proves
            </Box>
            <Box
              component="th"
              sx={{ ...head, textAlign: "right", width: 120 }}
            >
              Result
            </Box>
          </Box>
        </Box>
        <Box component="tbody">
          {RELEASE_CHECKS.map((c) => {
            const r = running ? "running" : st.results[c.id];
            return (
              <Box
                component="tr"
                key={c.id}
                sx={{ borderBottom: 1, borderColor: "divider" }}
              >
                <Box component="td" sx={{ ...cell, fontWeight: 500 }}>
                  {c.label}
                </Box>
                <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                  {c.proves}
                </Box>
                <Box
                  component="td"
                  sx={{
                    ...cell,
                    textAlign: "right",
                    color:
                      r === "passed"
                        ? "status.success.fg"
                        : r === "failed"
                          ? "status.danger.fg"
                          : "text.tertiary",
                  }}
                >
                  {r === "passed"
                    ? "Passed"
                    : r === "failed"
                      ? "Failed"
                      : r === "running"
                        ? "Checking…"
                        : "Pending"}
                </Box>
              </Box>
            );
          })}
        </Box>
      </Box>

      {/* ── what to do next ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 1.5, py: 1.25 }}
      >
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {locked
            ? `Locked ${new Date(locked.at).toLocaleDateString(undefined, { day: "numeric", month: "short" })} · train ${locked.splits.train.length} · test ${locked.splits.test.length} scenarios · scorer ${locked.rewardVersion}. A copy is editable and isn't comparable with these scores.`
            : st.stale
              ? `Checks last ran on ${st.checkedVersion}. This is ${st.version}, so they run again.`
              : st.ready
                ? "All five passed on this version. Lock it to freeze tasks, splits and the scorer."
                : "Run the checks on this version. Nothing is shown as Ready until all five pass."}
        </Typography>
        {locked ? (
          onCopy && (
            <Button
              size="small"
              variant="outlined"
              onClick={onCopy}
              startIcon={<Iconify icon="solar:copy-linear" width={14} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              Copy to edit
            </Button>
          )
        ) : (
          <>
            <Button
              size="small"
              variant="outlined"
              disabled={!patch || running}
              onClick={() => setRunning(true)}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              {st.checkedVersion ? "Run checks again" : "Run checks"}
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!patch || !st.ready || running}
              onClick={() => setConfirmLock(true)}
              startIcon={
                <Iconify icon="solar:lock-keyhole-linear" width={14} />
              }
              sx={{ flexShrink: 0 }}
            >
              Lock this release
            </Button>
          </>
        )}
      </Stack>

      {/* ── RL access: only on a locked release ── */}
      <Box sx={{ borderTop: 1, borderColor: "divider", px: 1.5, py: 1.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          RL access
        </Typography>
        {!locked ? (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Unavailable: lock this release first. A trainer only reaches a
            locked release, so its rewards stay comparable.
          </Typography>
        ) : (
          <RlAccess
            env={env}
            envState={envState}
            patch={patch}
            split={split}
            onSplit={setSplit}
          />
        )}
      </Box>

      {confirmLock && (
        <ConfirmDialog
          open
          onClose={() => setConfirmLock(false)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={`Lock release ${st.version}?`}
          content={
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Its tasks, train and test splits and scorer freeze, so every score
              on it stays comparable. It becomes read-only; copy it to make
              changes. RL access opens once it&apos;s locked.
            </Typography>
          }
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                setConfirmLock(false);
                patch?.(lockPatch(env, envState));
              }}
            >
              Lock release
            </Button>
          }
        />
      )}
    </SectionCard>
  );
}
ReleasePanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onCopy: PropTypes.func,
};

/* A trainer's way in: a split, an API key, the calls, and what it ran. */
function RlAccess({ env, envState, patch, split, onSplit }) {
  const locked = envState.lockedRelease;
  const keys = envState.rlKeys || [];
  const sessions = envState.rlSessions || [];
  const credits = sessions.reduce((n, s) => n + (s.credits || 0), 0);
  const cell = {
    typography: "s2",
    py: 0.75,
    px: 1,
    borderBottom: 1,
    borderColor: "divider",
  };
  return (
    <Stack spacing={1.5}>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        Your own trainer and model; we run the world, the simulated people and
        the reward. Sessions use the same credits as simulations.
      </Typography>
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
        <TextField
          select
          size="small"
          value={split}
          onChange={(e) => onSplit(e.target.value)}
          inputProps={{ "aria-label": "Split" }}
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="train">
            {`Train · ${locked.splits.train.length} tasks`}
          </MenuItem>
          <MenuItem value="test">
            {`Test · ${locked.splits.test.length} tasks`}
          </MenuItem>
        </TextField>
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {split === "test"
            ? "Test tasks are listed for evaluation; their answers and hidden checks are never returned."
            : "Train tasks are for learning; the reward comes from sub-goal checks and judged criteria."}
        </Typography>
      </Stack>

      <Stack direction="row" spacing={1.5} alignItems="center">
        <Typography
          sx={{ typography: "s2", color: "text.secondary", width: 72 }}
        >
          API key
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontFamily: "monospace",
            flex: 1,
            minWidth: 0,
          }}
          noWrap
        >
          {keys.length ? keys[0].masked : "None yet"}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={!patch}
          onClick={() => patch?.(rlKeyPatch(envState, split))}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {keys.length ? "New key" : "Create API key"}
        </Button>
      </Stack>

      <CodeBlock code={rlCodeOf(env, envState, split)} />

      <Box>
        <Stack direction="row" alignItems="baseline" sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            Sessions
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {`${credits.toLocaleString()} credits used`}
          </Typography>
        </Stack>
        {sessions.length === 0 ? (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            No sessions yet. They show here as a trainer opens them.
          </Typography>
        ) : (
          <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
            <Box component="thead">
              <Box component="tr">
                {["Task", "Split", "Status", "Final reward"].map((h, i) => (
                  <Box
                    component="th"
                    key={h}
                    sx={{
                      ...cell,
                      typography: "s3",
                      color: "text.tertiary",
                      fontWeight: 500,
                      textAlign: i === 3 ? "right" : "left",
                    }}
                  >
                    {h}
                  </Box>
                ))}
              </Box>
            </Box>
            <Box component="tbody">
              {sessions.map((s) => (
                <Box component="tr" key={s.id}>
                  <Box component="td" sx={cell}>
                    {s.task}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {s.split === "test" ? "Test" : "Train"}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {s.status}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                      color:
                        s.reward == null ? "text.tertiary" : "text.primary",
                    }}
                  >
                    {s.reward == null
                      ? s.rewardNote || "—"
                      : s.reward.toFixed(2)}
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>
        )}
      </Box>
    </Stack>
  );
}
RlAccess.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  split: PropTypes.string.isRequired,
  onSplit: PropTypes.func.isRequired,
};
