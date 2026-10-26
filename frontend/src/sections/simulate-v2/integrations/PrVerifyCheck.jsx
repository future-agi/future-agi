import PropTypes from "prop-types";
import { useDemoMode } from "../agents/demoMode";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  CHECK_NAME,
  checksOf,
  isPending,
  latestCheck,
  rerunCheck,
  resultLine,
  simulatePush,
  statusOf,
  useNow,
} from "../_mock/prChecks";
import { ago } from "../agents/agentViewUtils";
import { DemoLabel } from "../agents/AgentBits";

const MONO = "ui-monospace, Menlo, monospace";
const ICON = {
  queued: { icon: "solar:clock-circle-linear", color: "text.subtitle" },
  running: { icon: "eos-icons:three-dots-loading", color: "text.secondary" },
  passed: { icon: "solar:check-circle-bold", color: "status.success.fg" },
  failed: { icon: "solar:close-circle-bold", color: "status.danger.fg" },
};
const LINK = { color: "text.secondary", minWidth: 0 };

/**
 * The verify check as GitHub shows it on the PR: the run on the head commit,
 * what it re-ran, and whether it still matches the proof. Earlier runs —
 * one per push — fold away under the count. `now` comes from the card so the
 * row and the merge button agree on the status.
 */
export default function PrVerifyCheck({ pr, now }) {
  const demo = useDemoMode();
  const [open, setOpen] = useState(false);
  const runs = checksOf(pr);
  const run = runs.at(-1);
  if (!run) return null;
  const status = statusOf(run, now);
  const settled = !isPending(status);

  return (
    <Box
      sx={{
        mt: 1.25,
        borderRadius: 1,
        bgcolor: (t) => alpha(t.palette.text.primary, 0.04),
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1}
        sx={{ px: 1.25, py: 0.875 }}
      >
        <Iconify
          icon={ICON[status].icon}
          width={16}
          sx={{ color: ICON[status].color, mt: 0.25, flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={1}>
            <Typography
              sx={{ typography: "s3", fontWeight: 600, whiteSpace: "nowrap" }}
            >
              {CHECK_NAME}
            </Typography>
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.subtitle",
                flex: 1,
                minWidth: 0,
              }}
            >
              <Box
                component="span"
                sx={{ fontFamily: MONO, color: "text.secondary" }}
              >
                {run.sha}
              </Box>
              {` · ${run.scenarios} scenarios · ${run.repeats} repeats`}
            </Typography>
            <Tooltip
              arrow
              placement="top"
              title="The improvement's scenarios, re-run on the PR's head commit and compared with its proof."
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  whiteSpace: "nowrap",
                  cursor: "help",
                }}
              >
                Re-runs on every push
              </Typography>
            </Tooltip>
          </Stack>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ mt: 0.25 }}
          >
            <Typography
              sx={{
                typography: "s3",
                color:
                  status === "failed" ? "status.danger.fg" : "text.secondary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {resultLine(run, status)}
            </Typography>
            {runs.length > 1 && (
              <Button
                size="small"
                onClick={() => setOpen((o) => !o)}
                endIcon={
                  <Iconify
                    icon={
                      open
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={12}
                  />
                }
                sx={{ ...LINK, "& .MuiButton-endIcon": { ml: 0.25 } }}
              >
                {`${runs.length} runs`}
              </Button>
            )}
            {pr.status === "open" && settled && status === "failed" && (
              <Button size="small" onClick={() => rerunCheck(pr)} sx={LINK}>
                Re-run
              </Button>
            )}
            {/* No GitHub on the other end — this stands in for someone pushing to the branch. */}
            {demo && pr.status === "open" && settled && (
              <Button
                size="small"
                onClick={() => simulatePush(pr)}
                sx={{ ...LINK, color: "text.subtitle" }}
              >
                <DemoLabel />
                Simulate a new push
              </Button>
            )}
          </Stack>
        </Box>
      </Stack>

      {open && runs.length > 1 && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            px: 1.25,
            py: 0.75,
            pl: 4,
          }}
        >
          {[...runs].reverse().map((r) => {
            const s = statusOf(r, now);
            return (
              <Stack
                key={r.id}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ py: 0.25 }}
              >
                <Iconify
                  icon={ICON[s].icon}
                  width={14}
                  sx={{ color: ICON[s].color, flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.secondary",
                    width: 64,
                    flexShrink: 0,
                  }}
                >
                  {r.sha}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {`${r.push === 0 ? "Opened — proof commit" : `Push ${r.push}`} · ${s === "failed" ? `must-pass “${r.failure?.mustPass}” regressed` : s}`}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    whiteSpace: "nowrap",
                  }}
                >
                  {ago(r.queuedAt)}
                </Typography>
              </Stack>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

PrVerifyCheck.propTypes = {
  pr: PropTypes.object.isRequired,
  now: PropTypes.number,
};

/** The check in one word, for lists of PRs. Ticks on its own while a run is pending. */
export function PrVerifyStatus({ pr }) {
  const run = latestCheck(pr);
  const now = useNow(!!run && isPending(statusOf(run, Date.now())));
  if (!run) return null;
  const status = statusOf(run, now);
  return (
    <Tooltip
      arrow
      placement="top"
      title={`${CHECK_NAME} on ${run.sha} — ${resultLine(run, status)}`}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ flexShrink: 0, cursor: "default" }}
      >
        <Iconify
          icon={ICON[status].icon}
          width={14}
          sx={{ color: ICON[status].color }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: status === "failed" ? "status.danger.fg" : "text.secondary",
            whiteSpace: "nowrap",
          }}
        >{`verify ${status}`}</Typography>
      </Stack>
    </Tooltip>
  );
}
PrVerifyStatus.propTypes = { pr: PropTypes.object.isRequired };
