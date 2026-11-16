import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

/*
  Trial routes (proposal 8, 16 Nov): every trial of this scenario on one
  strip. Columns are steps matched by who asked whom; the first column where
  the trials went differently is shaded, and a line says what differs, with a
  link to put two trials side by side. Not in Nikhil's plan — a proposal.
*/

const linkSx = {
  typography: "s3",
  fontWeight: 500,
  color: "text.primary",
  p: 0,
  border: 0,
  bgcolor: "transparent",
  cursor: "pointer",
  whiteSpace: "nowrap",
  textDecoration: "underline",
  textUnderlineOffset: "2px",
  textDecorationColor: (t) => t.palette.divider,
  "&:hover": { textDecorationColor: "currentColor" },
};

function Cell({ cell, onClick }) {
  const none = cell.n == null;
  return (
    <Box
      component={none ? "span" : "button"}
      type={none ? undefined : "button"}
      onClick={none ? undefined : onClick}
      title={none ? undefined : `Step ${cell.n} · ${cell.state}`}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        height: 20,
        px: 0.75,
        border: 0,
        borderRadius: "10px",
        typography: "s3",
        fontWeight: 500,
        whiteSpace: "nowrap",
        cursor: none ? "default" : "pointer",
        fontVariantNumeric: "tabular-nums",
        bgcolor: (t) =>
          none
            ? "transparent"
            : cell.bad
              ? alpha(t.palette.error.main, 0.1)
              : t.palette.background.neutral,
        color: none
          ? "text.disabled"
          : cell.bad
            ? "error.main"
            : "text.secondary",
      }}
    >
      {none
        ? "not asked"
        : `${cell.n} · ${cell.text}${cell.turns ? ` · ×${cell.turns}` : ""}`}
    </Box>
  );
}
Cell.propTypes = { cell: PropTypes.object.isRequired, onClick: PropTypes.func };

export default function TrialRoutes({
  routes,
  current,
  showTools,
  onToggleTools,
  onOpen,
  onCompare,
}) {
  const { columns, rows, split } = routes;
  const cur = rows.find((r) => r.idx === current) || rows[0];
  /* Who to compare with: a trial that ended differently, else any other. */
  const other =
    rows.find((r) => r.idx !== cur.idx && r.status !== cur.status) ||
    rows.find((r) => r.idx !== cur.idx);
  const tint = (t) => alpha(t.palette.warning.main, 0.08);
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 2, pt: 1, pb: 0.75 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`Trial routes · ${rows.length} repeats of this scenario`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Steps matched across trials by who asked whom
            {showTools ? "" : " · tool calls folded"}
          </Typography>
        </Box>
        <Box
          component="button"
          type="button"
          aria-pressed={showTools}
          onClick={onToggleTools}
          sx={{
            height: 24,
            px: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            bgcolor: showTools ? "action.selected" : "background.paper",
            typography: "s3",
            fontWeight: 500,
            color: "text.primary",
            cursor: "pointer",
          }}
        >
          Show tool calls
        </Box>
      </Stack>
      <Box sx={{ overflowX: "auto", px: 2 }}>
        <Box
          component="table"
          sx={{
            borderCollapse: "collapse",
            minWidth: "100%",
            "& th, & td": {
              px: 0.75,
              py: 0.625,
              borderBottom: "1px solid",
              borderColor: "divider",
              textAlign: "left",
              whiteSpace: "nowrap",
            },
            "& th": {
              typography: "s3",
              fontSize: 10.5,
              fontWeight: 600,
              color: "text.disabled",
              letterSpacing: "0.03em",
              textTransform: "uppercase",
              maxWidth: 150,
              overflow: "hidden",
              textOverflow: "ellipsis",
            },
          }}
        >
          <thead>
            <tr>
              <th>Trial</th>
              <th>Result</th>
              {columns.map((c) => (
                <Box
                  component="th"
                  key={c.key}
                  title={c.label}
                  sx={c.key === split?.key ? { bgcolor: tint } : undefined}
                >
                  {c.label}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isCur = r.idx === cur.idx;
              return (
                <tr key={r.idx}>
                  <td>
                    <Box
                      component="button"
                      type="button"
                      onClick={() => onOpen(r.idx)}
                      sx={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.75,
                        p: 0,
                        border: 0,
                        bgcolor: "transparent",
                        cursor: "pointer",
                        typography: "s2",
                        fontWeight: isCur ? 600 : 400,
                        color: isCur ? "text.primary" : "text.secondary",
                      }}
                    >
                      <Box
                        sx={{
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          bgcolor:
                            r.status === "passed"
                              ? "success.main"
                              : "error.main",
                        }}
                      />
                      {`Trial ${r.idx + 1}`}
                    </Box>
                  </td>
                  <td>
                    <Typography
                      component="span"
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color:
                          r.status === "passed" ? "success.main" : "error.main",
                      }}
                    >
                      {r.status === "passed" ? "Passed" : "Failed"}
                    </Typography>
                    {r.result && (
                      <Typography
                        component="span"
                        title={r.result.text}
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {` · sub-goal at ${r.result.at}`}
                      </Typography>
                    )}
                  </td>
                  {columns.map((c) => (
                    <Box
                      component="td"
                      key={c.key}
                      sx={c.key === split?.key ? { bgcolor: tint } : undefined}
                    >
                      <Cell
                        cell={r.cells[c.key]}
                        onClick={() => onOpen(r.idx, r.cells[c.key].n)}
                      />
                    </Box>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </Box>
      </Box>
      <Stack
        direction="row"
        spacing={1}
        alignItems="flex-start"
        useFlexGap
        flexWrap="wrap"
        sx={{
          px: 2,
          py: 0.75,
          bgcolor: split ? tint : "transparent",
        }}
      >
        <Iconify
          icon="solar:routing-2-linear"
          width={14}
          sx={{ mt: "2px", color: "text.secondary" }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1 }}>
          {split ? (
            <>
              <Box
                component="span"
                sx={{ fontWeight: 600, color: "text.primary" }}
              >
                {`First split: ${split.label}. `}
              </Box>
              {split.text}
            </>
          ) : (
            "Every trial took the same route."
          )}
        </Typography>
        {other && (
          <Typography
            component="button"
            type="button"
            onClick={() => onCompare(other.idx)}
            sx={linkSx}
          >
            {`Compare trial ${cur.idx + 1} with trial ${other.idx + 1}`}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

TrialRoutes.propTypes = {
  routes: PropTypes.object.isRequired,
  current: PropTypes.number.isRequired,
  showTools: PropTypes.bool,
  onToggleTools: PropTypes.func.isRequired,
  /* (trialIdx, step?) → open that trial, on that step. */
  onOpen: PropTypes.func.isRequired,
  onCompare: PropTypes.func.isRequired,
};
