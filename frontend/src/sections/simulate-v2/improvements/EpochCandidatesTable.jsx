import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import { Box, Link, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { focusRing } from "../components/clickable";

const RED = "status.danger.fg";
const GREEN = "status.success.fg";
const num = { fontVariantNumeric: "tabular-nums" };
const COLS =
  "minmax(112px, 136px) minmax(240px, 1fr) 132px 132px minmax(180px, 240px) 112px";

/**
 * Every candidate an epoch tried, grouped by where the epoch stopped it:
 * the winner, the ones that passed and weren't picked, then each step that
 * rejected one. Each group folds; the ones worth reading start open.
 * Target scenarios sort high to low inside a group.
 */
export default function EpochCandidatesTable({ groups, onSeeChange }) {
  const [open, setOpen] = useState(() =>
    Object.fromEntries(groups.map((g) => [g.id, !!g.open])),
  );
  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  const head = {
    typography: "s3",
    fontWeight: 600,
    color: "text.tertiary",
    whiteSpace: "nowrap",
  };
  const cell = { px: 1.5, minWidth: 0, display: "flex", alignItems: "center" };

  return (
    <Box
      role="table"
      aria-label="Candidates"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "auto",
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ minWidth: 960 }}>
        <Box
          role="row"
          sx={{
            display: "grid",
            gridTemplateColumns: COLS,
            height: TABLE_HEADER_HEIGHT.default,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box role="columnheader" sx={{ ...cell, ...head, pl: 2 }}>
            Candidate
          </Box>
          <Box role="columnheader" sx={{ ...cell, ...head }}>
            What it tried
          </Box>
          <Tooltip
            arrow
            title="Pass rate on the scenarios the epoch tuned on, against the agent before the change. Sorted high to low in each group."
          >
            <Box
              role="columnheader"
              aria-sort="descending"
              sx={{
                ...cell,
                ...head,
                justifyContent: "flex-end",
                color: "text.primary",
                gap: 0.5,
              }}
            >
              Target scenarios
              <Iconify icon="solar:arrow-down-linear" width={12} />
            </Box>
          </Tooltip>
          <Tooltip
            arrow
            title="Scenarios kept out of the search. Only finalists run on them."
          >
            <Box role="columnheader" sx={{ ...cell, ...head }}>
              Held back
            </Box>
          </Tooltip>
          <Box role="columnheader" sx={{ ...cell, ...head }}>
            Result
          </Box>
          <Box role="columnheader" sx={{ ...cell, ...head, pr: 2 }} />
        </Box>

        {groups.map((g) => (
          <Fragment key={g.id}>
            <Box
              role="row"
              component="button"
              type="button"
              onClick={() => toggle(g.id)}
              aria-expanded={!!open[g.id]}
              sx={{
                all: "unset",
                boxSizing: "border-box",
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                height: ROW_HEIGHT.default,
                px: 2,
                cursor: "pointer",
                bgcolor: "background.neutral",
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
                ...focusRing,
              }}
            >
              <Iconify
                icon={
                  open[g.id]
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={12}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography sx={{ typography: "s2", fontWeight: 500, flex: 1 }}>
                {g.label}
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", ...num }}
              >
                {g.rows.length}
              </Typography>
            </Box>
            {open[g.id] &&
              g.rows.map((c) => (
                <Box
                  key={c.n}
                  role="row"
                  sx={{
                    display: "grid",
                    gridTemplateColumns: COLS,
                    height: ROW_HEIGHT.default,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Box role="cell" sx={{ ...cell, pl: 2 }}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 500 }}
                    >
                      {c.name}
                    </Typography>
                  </Box>
                  <Box role="cell" sx={cell}>
                    <Tooltip
                      arrow
                      title={c.tried || ""}
                      enterDelay={400}
                      placement="bottom-start"
                    >
                      <Typography
                        noWrap
                        sx={{ typography: "s2", color: "text.secondary" }}
                      >
                        {c.tried || "—"}
                      </Typography>
                    </Tooltip>
                  </Box>
                  <Box
                    role="cell"
                    sx={{ ...cell, justifyContent: "flex-end", gap: 0.75 }}
                  >
                    <Typography noWrap sx={{ typography: "s2", ...num }}>
                      {`${c.score}%`}
                    </Typography>
                    {c.delta != null && (
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          fontWeight: 500,
                          ...num,
                          width: 52,
                          color:
                            c.delta > 0
                              ? GREEN
                              : c.delta < 0
                                ? RED
                                : "text.tertiary",
                        }}
                      >
                        {c.delta === 0
                          ? "same"
                          : `${c.delta > 0 ? "↑" : "↓"} ${Math.abs(c.delta)} pts`}
                      </Typography>
                    )}
                  </Box>
                  <Box role="cell" sx={cell}>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        ...num,
                        color: c.held ? "text.secondary" : "text.tertiary",
                      }}
                    >
                      {c.held
                        ? `${c.held.from}% → ${c.held.to}%${c.held.projected ? " projected" : ""}`
                        : "Not run"}
                    </Typography>
                  </Box>
                  <Box role="cell" sx={cell}>
                    {c.stage === "winner" ? (
                      <StatusBadge
                        status="passed"
                        role="success"
                        label="Winner"
                      />
                    ) : (
                      <Tooltip
                        arrow
                        title={c.note && c.note !== c.result ? c.note : ""}
                        enterDelay={300}
                      >
                        <Typography
                          noWrap
                          sx={{
                            typography: "s2",
                            color: c.tone === "broke" ? RED : "text.secondary",
                          }}
                        >
                          {c.result}
                        </Typography>
                      </Tooltip>
                    )}
                  </Box>
                  <Box
                    role="cell"
                    sx={{ ...cell, justifyContent: "flex-end", pr: 2 }}
                  >
                    {c.proposals.length > 0 && (
                      <Link
                        component="button"
                        type="button"
                        underline="hover"
                        onClick={() => onSeeChange(c)}
                        sx={{
                          typography: "s2",
                          fontWeight: 500,
                          whiteSpace: "nowrap",
                        }}
                      >
                        See its change
                      </Link>
                    )}
                  </Box>
                </Box>
              ))}
          </Fragment>
        ))}
      </Box>
    </Box>
  );
}

EpochCandidatesTable.propTypes = {
  /* candidateGroupsOf: [{ id, label, open, rows }] */
  groups: PropTypes.array.isRequired,
  onSeeChange: PropTypes.func.isRequired,
};
