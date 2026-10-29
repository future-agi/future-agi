import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";
import { CAPABILITIES, COMPAT_STATUS } from "../_mock/compatibility";
import {
  FAMILY_CATEGORIES,
  TESTED_VERSIONS,
  diagnosticOf,
} from "../_mock/testedVersions";

const MONO = "ui-monospace, Menlo, monospace";
const COLS =
  "minmax(170px, 1.2fr) minmax(150px, 1.1fr) 104px minmax(0, 1.6fr) 120px";
const STATUS_KEY = {
  Supported: "supported",
  Partial: "partial",
  Untested: "untested",
};
const when = (iso) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

/* One family: its tested versions and limits; its diagnostic one click away. */
function FamilyRow({ row, mine }) {
  const [open, setOpen] = useState(mine);
  const diag = diagnosticOf(row);
  const status = STATUS_KEY[row.status] || "untested";
  return (
    <Box
      sx={{
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
        ...(mine && { bgcolor: "action.hover" }),
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: COLS,
          columnGap: 2,
          px: 1.5,
          py: 0.875,
          alignItems: "start",
        }}
      >
        <Box minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {row.family}
            {mine && (
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.tertiary", ml: 0.75 }}
              >
                this agent
              </Box>
            )}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {row.how}
          </Typography>
        </Box>
        <Typography sx={{ typography: "s3", fontFamily: MONO, minWidth: 0 }}>
          {row.versions}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <StatusDot status={COMPAT_STATUS[status].dot} size={6} live={false} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {row.status}
          </Typography>
        </Stack>
        <Typography
          sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
        >
          {[
            row.evidence !== "—" && row.evidence,
            row.limits !== "—" && `Not covered: ${row.limits}`,
          ]
            .filter(Boolean)
            .join(". ")}
        </Typography>
        {diag.id ? (
          <Box
            {...clickable(() => setOpen((o) => !o), { expanded: open })}
            sx={{
              ...focusRing,
              cursor: "pointer",
              typography: "s3",
              fontFamily: MONO,
              textAlign: "right",
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {diag.id}
          </Box>
        ) : (
          <Typography
            sx={{
              typography: "s3",
              color: "text.disabled",
              textAlign: "right",
            }}
          >
            No diagnostic
          </Typography>
        )}
      </Box>
      <Collapse in={open && !!diag.id} unmountOnExit>
        <Box sx={{ px: 1.5, pb: 1.25 }}>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
          >
            {`Diagnostic ${diag.id} · ${diag.at ? when(diag.at) : ""} · the reference agent for this family, on the returns world`}
          </Typography>
          <Stack
            direction="row"
            flexWrap="wrap"
            useFlexGap
            columnGap={2}
            rowGap={0.5}
          >
            {CAPABILITIES.filter((c) => diag.results[c.id]).map((c) => (
              <Stack
                key={c.id}
                direction="row"
                alignItems="center"
                spacing={0.5}
              >
                <StatusDot
                  status={COMPAT_STATUS[diag.results[c.id]].dot}
                  size={5}
                  live={false}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${c.label} · ${COMPAT_STATUS[diag.results[c.id]].label.toLowerCase()}`}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Collapse>
    </Box>
  );
}
FamilyRow.propTypes = {
  row: PropTypes.object.isRequired,
  mine: PropTypes.bool,
};

/**
 * The tested-versions matrix (roadmap release.md, Week 2): exact versions per
 * agent family, grouped the way the roadmap lists them, each linked to the
 * diagnostic behind its status. Untested is untested — never shown as
 * supported, and a family not listed may still connect, uncertified.
 */
export default function TestedVersionsMatrix({ highlight = null }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflowX: "auto",
      }}
    >
      <Box sx={{ minWidth: 760 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: COLS,
            columnGap: 2,
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {[
            "Family",
            "Tested versions",
            "Status",
            "Evidence",
            "Diagnostic",
          ].map((h, i) => (
            <Typography
              key={h}
              sx={{
                typography: "s3",
                color: "text.tertiary",
                textAlign: i === 4 ? "right" : "left",
              }}
            >
              {h}
            </Typography>
          ))}
        </Box>
        {FAMILY_CATEGORIES.map((cat) => {
          const rows = TESTED_VERSIONS.filter((r) => r.category === cat.id);
          if (!rows.length) return null;
          return (
            <Box
              key={cat.id}
              sx={{
                "& + &": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Typography
                sx={{
                  px: 1.5,
                  pt: 1,
                  pb: 0.25,
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                }}
              >
                {cat.label}
              </Typography>
              {rows.map((r) => (
                <FamilyRow
                  key={r.family}
                  row={r}
                  mine={highlight === r.family}
                />
              ))}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
TestedVersionsMatrix.propTypes = {
  /* The family this agent belongs to — opened and marked. */
  highlight: PropTypes.string,
};
