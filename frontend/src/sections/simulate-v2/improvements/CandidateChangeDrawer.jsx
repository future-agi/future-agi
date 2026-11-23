import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import SideDrawer from "../components/SideDrawer";
import WinnerChanges from "../run/fixmyagent/WinnerChanges";

const RED = "status.danger.fg";
const num = { fontVariantNumeric: "tabular-nums" };

/**
 * One candidate's change, beside the epoch: where the epoch stopped it, its
 * numbers, and each file it changes with why — the same diff the winner's
 * Changes tab shows. Its run (the calls behind its target score) is a click
 * away.
 */
export default function CandidateChangeDrawer({
  candidate,
  env,
  record,
  onClose,
  onOpenRun,
}) {
  const c = candidate;
  const rows = c
    ? [
        {
          k: "Target scenarios",
          v: `${c.score}%${c.delta != null && c.delta !== 0 ? ` · ${c.delta > 0 ? "↑" : "↓"} ${Math.abs(c.delta)} pts` : ""}`,
        },
        {
          k: "Held back",
          v: c.held
            ? `${c.held.from}% → ${c.held.to}%${c.held.projected ? " projected" : ""}`
            : "Not run",
        },
        {
          k: "Result",
          v:
            c.note && c.stage !== "winner"
              ? `${c.result} · ${c.note}`
              : c.result,
          tone: c.tone,
        },
      ]
    : [];
  return (
    <SideDrawer open={!!c} onClose={onClose} width={720}>
      {c && (
        <Stack sx={{ height: "100%" }}>
          <Stack
            direction="row"
            alignItems="flex-start"
            spacing={1}
            sx={{
              px: 2.5,
              py: 2,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                  {`${c.name}'s change`}
                </Typography>
                {c.stage === "winner" && (
                  <StatusBadge status="passed" role="success" label="Winner" />
                )}
              </Stack>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {c.tried || ""}
              </Typography>
            </Box>
            {onOpenRun && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onOpenRun(c)}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                Open its run
              </Button>
            )}
            <IconButton size="small" onClick={onClose} aria-label="Close">
              <Iconify
                icon="solar:close-linear"
                width={20}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Stack>
          <Box sx={{ overflowY: "auto", flex: 1 }}>
            <Box
              sx={{
                px: 2.5,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              {rows.map((r) => (
                <Stack key={r.k} direction="row" spacing={2} sx={{ py: 0.5 }}>
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.tertiary",
                      width: 128,
                      flexShrink: 0,
                    }}
                  >
                    {r.k}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      ...num,
                      minWidth: 0,
                      color: r.tone === "broke" ? RED : "text.primary",
                    }}
                  >
                    {r.v}
                  </Typography>
                </Stack>
              ))}
            </Box>
            <WinnerChanges
              env={env}
              winner={{ proposals: c.proposals }}
              record={record}
            />
          </Box>
        </Stack>
      )}
    </SideDrawer>
  );
}

CandidateChangeDrawer.propTypes = {
  /* A row from candidateRowsOf, or null when closed. */
  candidate: PropTypes.object,
  env: PropTypes.object,
  record: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onOpenRun: PropTypes.func,
};
