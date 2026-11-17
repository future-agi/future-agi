import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

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
        : /* What happened, not how long (Chintan, 17 Nov): the step, and
             what went wrong instead when it did (the step is on hover). */
          `${cell.bad ? `${cell.state.charAt(0).toUpperCase()}${cell.state.slice(1)}` : `Step ${cell.n}`}${cell.turns ? ` · ×${cell.turns}` : ""}`}
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
  const [open, setOpen] = useState(false);
  const { columns, rows, split } = routes;
  const cur = rows.find((r) => r.idx === current) || rows[0];
  /* Who to compare with: a trial that ended differently, else any other. */
  const other =
    rows.find((r) => r.idx !== cur.idx && r.status !== cur.status) ||
    rows.find((r) => r.idx !== cur.idx);
  const tint = (t) => alpha(t.palette.warning.main, 0.08);
  /* "no answer in 1, 3 · answered in 2" — each outcome once, not per trial. */
  const splitWords = split
    ? Object.entries(
        rows.reduce((acc, r) => {
          const st = r.cells[split.key]?.state || "not asked";
          (acc[st] = acc[st] || []).push(r.idx + 1);
          return acc;
        }, {}),
      )
        .map(([st, ns]) => `${st} in ${ns.join(", ")}`)
        .join(" · ")
    : "";
  /* One line by default (17 Nov, "too verbose"): the trials as dots, where
     they first went differently, and Compare; the table on demand. */
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
        sx={{ px: 2, py: 0.75 }}
      >
        {/* Trials are switched only in the drawer header (Nosang, 17 Nov), so
            no second picker here. Proposal status lives on the review page,
            never in the product UI. */}
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${rows.length} trials`}
        </Typography>
        <Typography
          sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 200 }}
        >
          {split ? (
            <>
              {"Split at "}
              <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
                {split.label}
              </Box>
              {`: ${splitWords}`}
            </>
          ) : (
            "Every trial took the same route"
          )}
        </Typography>
        {other && split && (
          <Typography
            component="button"
            type="button"
            onClick={() => onCompare(other.idx)}
            sx={linkSx}
          >
            {`Compare ${cur.idx + 1} with ${other.idx + 1}`}
          </Typography>
        )}
        <Typography
          component="button"
          type="button"
          onClick={() => setOpen((v) => !v)}
          sx={{ ...linkSx, color: "text.secondary" }}
        >
          {open ? "Hide trials" : "Show trials"}
        </Typography>
      </Stack>
      {open && (
        <>
          <Stack direction="row" justifyContent="flex-end" sx={{ px: 2, pb: 0.5 }}>
            <Box
              component="button"
              type="button"
              aria-pressed={showTools}
              onClick={onToggleTools}
              sx={{
                height: 22,
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
      <Box sx={{ overflowX: "auto", px: 2, pb: 1 }}>
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
        </>
      )}
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
