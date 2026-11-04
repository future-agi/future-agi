import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Builder → Review design (builder.md). The proposal — world, data, tools,
  rules, people and scenario coverage — waits for approval; only approval
  starts the cloud build. Auto mode carries out approved steps without
  stopping, but never skips this approval. A question left past the waiting
  limit is Parked; Continue reopens the same proposal, not a new one.
*/
export default function DesignReview({
  env,
  draftCount,
  waiting,
  parked,
  since,
  autoMode,
  onApprove,
  onContinue,
  framed = false,
}) {
  const rows = [
    [
      "World",
      `${(env?.seed?.tables || []).length} tables · ${(env?.seed?.tables || []).reduce((n, t) => n + (t.rows || 0), 0).toLocaleString()} seeded rows`,
    ],
    ["Tools", `${(env?.tools || []).length} the world answers`],
    ["Rules", `${(env?.rules || []).length} graded on every run`],
    [
      "People",
      `${(env?.actors || []).length || "No"} simulated ${(env?.actors || []).length === 1 ? "person" : "people"} besides the caller`,
    ],
    ["Scenarios", `${draftCount} to write and check`],
  ];
  const status = parked ? "Parked" : waiting ? "Awaiting approval" : "Approved";
  /* Framed: the full-width card above every tab, so the one thing to do
     shows wherever the person is — chat folded or not, any tab. */
  if (framed)
    return (
      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
            "& > div": { borderBottom: 0 },
          }}
        >
          <DesignReview
            env={env}
            draftCount={draftCount}
            waiting={waiting}
            parked={parked}
            since={since}
            autoMode={autoMode}
            onApprove={onApprove}
            onContinue={onContinue}
          />
        </Box>
      </Box>
    );
  return (
    <Box
      sx={{
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        px: 2,
        py: 1.5,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          Review design
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: parked
              ? "status.warning.fg"
              : waiting
                ? "text.secondary"
                : "status.success.fg",
          }}
        >
          {status}
        </Typography>
      </Stack>

      {waiting && (
        <Box
          component="table"
          sx={{ width: 1, borderCollapse: "collapse", mb: 1 }}
        >
          <Box component="tbody">
            {rows.map(([k, v]) => (
              <Box component="tr" key={k}>
                <Box
                  component="td"
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    width: 84,
                    py: 0.25,
                    verticalAlign: "top",
                  }}
                >
                  {k}
                </Box>
                <Box
                  component="td"
                  sx={{ typography: "s2", color: "text.primary", py: 0.25 }}
                >
                  {v}
                </Box>
              </Box>
            ))}
          </Box>
        </Box>
      )}

      <Typography
        sx={{ typography: "s3", color: "text.tertiary", mb: waiting ? 1 : 0.5 }}
      >
        {parked
          ? `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}. Continue picks up this same proposal.`
          : waiting
            ? "Nothing is built until you approve. To change something first, ask the builder."
            : "Approved — the build is running from this design."}
      </Typography>

      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {autoMode
            ? "Auto mode: approved steps run without stopping. It never approves a design for you."
            : "Manual: each approved step waits for you to start it."}
        </Typography>
        {parked ? (
          <Button
            size="small"
            variant="outlined"
            onClick={onContinue}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Continue
          </Button>
        ) : (
          waiting && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onApprove}
              startIcon={
                <Iconify icon="solar:check-circle-linear" width={14} />
              }
            >
              Approve and build
            </Button>
          )
        )}
      </Stack>
    </Box>
  );
}
DesignReview.propTypes = {
  env: PropTypes.object,
  draftCount: PropTypes.number,
  waiting: PropTypes.bool,
  parked: PropTypes.bool,
  since: PropTypes.number,
  autoMode: PropTypes.bool,
  onApprove: PropTypes.func,
  onContinue: PropTypes.func,
  framed: PropTypes.bool,
};
