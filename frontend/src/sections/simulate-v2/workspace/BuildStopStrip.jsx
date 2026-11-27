import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Collapse,
  Link,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import LogExcerpt from "../components/logs/LogExcerpt";
import LogsDrawer from "../components/logs/LogsDrawer";
import { buildLogOf, firstErrorOf } from "../_mock/sandboxLogs";

const linkSx = {
  typography: "s3",
  fontWeight: 600,
  color: "text.primary",
  verticalAlign: "baseline",
};

/* Each phase's marker: done is the only colour; stopped and not run stay quiet. */
const MARK = {
  done: { icon: "solar:check-circle-linear", color: "status.success.fg" },
  stopped: { icon: "solar:stop-circle-linear", color: "text.primary" },
  next: { icon: "solar:clock-circle-linear", color: "text.tertiary" },
};

/**
 * A first build that stopped part-way, above every tab — read the way an
 * epoch that stopped reads (EpochEnding): the neutral status, one sentence on
 * what stopped and why, what's kept and spent, one way on and at most one
 * change that can fix it. Under it the three phases: done (kept), the one
 * that stopped, and the ones not run. The sandbox log excerpt is behind Show details.
 */
export default function BuildStopStrip({ view, build, agent, onRetry, onFix }) {
  const [open, setOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const log = useMemo(
    () => (build ? buildLogOf({ build, agent }) : null),
    [build, agent],
  );
  const focusLineId = firstErrorOf(log)?.line?.id;
  const { fix } = view;
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Box
        data-testid="build-stop-strip"
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
          gap={1.5}
          sx={{ px: 2, py: 1.25 }}
        >
          <Box sx={{ flexShrink: 0, alignSelf: "flex-start", pt: "1px" }}>
            <StatusBadge
              status="stopped"
              role="neutral"
              variant="badge"
              label="Build stopped"
            />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", color: "text.primary" }}>
              {view.sentence}
            </Typography>
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {view.fixed
                ? `Changed in the builder. ${view.kept} `
                : `${view.kept} `}
              {view.spent}
              {" · "}
              <Link
                component="button"
                type="button"
                underline="hover"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
                sx={linkSx}
              >
                {open ? "Hide details" : "Show details"}
              </Link>
            </Typography>
          </Box>
          {fix && onFix && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => onFix(fix)}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              {fix.label}
            </Button>
          )}
          <Tooltip arrow title={view.retry.tip}>
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!onRetry}
                onClick={onRetry}
                startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                {view.retry.label}
              </Button>
            </span>
          </Tooltip>
        </Stack>

        {/* The three phases, as a spec sheet: what's kept, where it stopped, what didn't run. */}
        <Box
          component="table"
          sx={{
            width: "100%",
            borderCollapse: "collapse",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box component="tbody">
            {view.phases.map((p) => {
              const mark = MARK[p.state];
              return (
                <Box
                  component="tr"
                  key={p.id}
                  sx={{
                    "& + tr > td": {
                      borderTop: "1px solid",
                      borderColor: "divider",
                    },
                  }}
                >
                  <Box
                    component="td"
                    sx={{ pl: 2, pr: 1, py: 0.75, width: 280 }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Iconify
                        icon={mark.icon}
                        width={14}
                        sx={{ color: mark.color, flexShrink: 0 }}
                      />
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: p.state === "stopped" ? 600 : 400,
                          color:
                            p.state === "next"
                              ? "text.tertiary"
                              : "text.primary",
                        }}
                      >
                        {p.label}
                      </Typography>
                    </Stack>
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      px: 1,
                      py: 0.75,
                      width: 96,
                      typography: "s3",
                      color: "text.tertiary",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {`Steps ${p.range}`}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      pl: 1,
                      pr: 2,
                      py: 0.75,
                      typography: "s3",
                      color:
                        p.state === "stopped"
                          ? "text.primary"
                          : "text.tertiary",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {p.state === "done" ? `Kept · ${p.note}` : p.note}
                  </Box>
                </Box>
              );
            })}
          </Box>
        </Box>

        <Collapse in={open} unmountOnExit>
          <Box
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              px: 2,
              py: 1.25,
            }}
          >
            <LogExcerpt log={log} onViewAll={() => setLogsOpen(true)} />
          </Box>
        </Collapse>
      </Box>
      <LogsDrawer
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        log={log}
        title={log?.title}
        subtitle={view.where}
        focusLineId={focusLineId}
      />
    </Box>
  );
}

BuildStopStrip.propTypes = {
  /* buildStopOf(): sentence, kept, spent, phases, retry, fix, log. */
  view: PropTypes.object.isRequired,
  /* The stopped versionBuild and the environment's agent: the sandbox log. */
  build: PropTypes.object,
  agent: PropTypes.object,
  onRetry: PropTypes.func,
  onFix: PropTypes.func,
};
