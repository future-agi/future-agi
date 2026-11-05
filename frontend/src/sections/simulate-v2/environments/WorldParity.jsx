import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { subsetLineOf } from "../_mock/catalogue";

/*
  Score parity for a public benchmark (T26): our hosted scores against the
  benchmark's own grader on a pinned reference run, including a task the
  reference is known to fail. A subset is labelled wherever it shows.
*/
const Row = ({ label, children }) => (
  <Stack
    direction="row"
    spacing={2}
    sx={{
      px: 1.5,
      py: 0.875,
      borderTop: "1px solid",
      borderColor: "divider",
      "&:first-of-type": { borderTop: 0 },
    }}
  >
    <Typography
      sx={{
        typography: "s2",
        color: "text.tertiary",
        width: 150,
        flexShrink: 0,
      }}
    >
      {label}
    </Typography>
    <Box sx={{ typography: "s2", color: "text.primary", minWidth: 0 }}>
      {children}
    </Box>
  </Stack>
);
Row.propTypes = { label: PropTypes.node, children: PropTypes.node };

const RESULT = { pass: "Pass", fail: "Fail" };

export default function WorldParity({ entry }) {
  const b = entry.benchmark;
  const p = b.parity;
  const ok = p.matched === p.total;
  const cell = {
    typography: "s2",
    py: 0.75,
    px: 1,
    borderBottom: 1,
    borderColor: "divider",
  };
  return (
    <Stack spacing={2}>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {`We run the benchmark here and score it with ${b.name}'s own grader. Before it's ready, a pinned reference run must score the same here as upstream, including a task it's known to fail.`}
      </Typography>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        <Row label="Upstream">{`${b.upstream} @ ${b.pin}`}</Row>
        <Row label="Grader">{b.grader}</Row>
        <Row label="Tasks">
          {subsetLineOf(entry)
            ? `${subsetLineOf(entry)} — scores are for this subset, not the full benchmark`
            : "All tasks"}
        </Row>
        <Row label="Score parity">
          <Box
            component="span"
            sx={{ color: ok ? "status.success.fg" : "status.danger.fg" }}
          >
            {ok ? "Matched" : "Not matched"}
          </Box>
          {` — ${p.matched} of ${p.total} tasks score the same as upstream on ${p.reference}, including ${p.knownFailures} known failure`}
        </Row>
      </Box>

      {p.mismatch && (
        <Box
          role="alert"
          sx={{
            border: 1,
            borderColor: "status.danger.fg",
            borderRadius: 1,
            px: 1.5,
            py: 1,
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${p.mismatch.task} scores differently here`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {p.mismatch.why}
          </Typography>
        </Box>
      )}

      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Reference run
        </Typography>
        <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
          <Box component="thead">
            <Box component="tr">
              {["Task", "Upstream grader", "Hosted here", "Match"].map(
                (h, i) => (
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
                ),
              )}
            </Box>
          </Box>
          <Box component="tbody">
            {[
              ...p.rows,
              ...(p.mismatch
                ? [
                    {
                      task: p.mismatch.task,
                      upstream: "pass",
                      hosted: "fail",
                      off: true,
                    },
                  ]
                : []),
            ].map((r) => (
              <Box component="tr" key={r.task}>
                <Box
                  component="td"
                  sx={{ ...cell, fontFamily: "ui-monospace, Menlo, monospace" }}
                >
                  {r.task}
                  {r.known && (
                    <Box
                      component="span"
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontFamily: "inherit",
                        ml: 1,
                      }}
                    >
                      known failure
                    </Box>
                  )}
                </Box>
                <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                  {RESULT[r.upstream]}
                </Box>
                <Box
                  component="td"
                  sx={{
                    ...cell,
                    color: r.off ? "status.danger.fg" : "text.secondary",
                  }}
                >
                  {RESULT[r.hosted]}
                </Box>
                <Box
                  component="td"
                  sx={{
                    ...cell,
                    textAlign: "right",
                    color: r.off ? "status.danger.fg" : "text.secondary",
                  }}
                >
                  {r.off ? "No" : "Yes"}
                </Box>
              </Box>
            ))}
          </Box>
        </Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {`Showing ${p.rows.length + (p.mismatch ? 1 : 0)} of ${p.total} tasks.`}
        </Typography>
      </Box>
    </Stack>
  );
}
WorldParity.propTypes = { entry: PropTypes.object.isRequired };
