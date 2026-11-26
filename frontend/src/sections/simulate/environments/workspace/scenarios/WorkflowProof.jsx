import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { WORKFLOW_PROOF_COPY as COPY } from "./scenarios.constants";

const KINDS = ["success", "refusal", "wrong_result"];

const headerCellSx = {
  typography: "s3",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
};

// Read only: per use case, how many scenarios prove the agent does it right, says no when it should, and that a
// deliberately wrong answer is caught. A gap is fixed by adding or validating scenarios on the tab.
export default function WorkflowProof({ workflows, versioned = false }) {
  // Proof needs versioning; while coverage loads the drawer's own state covers it.
  if (!versioned || !workflows) return null;
  return (
    <Box
      data-testid="workflow-proof"
      sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "m3", fontWeight: "fontWeightMedium" }}>
        {COPY.title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1 }}>
        {COPY.hint}
      </Typography>
      {workflows.length === 0 ? (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.empty}
        </Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell sx={headerCellSx}>{COPY.columns.workflow}</TableCell>
              {KINDS.map((kind) => (
                <TableCell key={kind} sx={headerCellSx} width={150}>
                  {COPY.columns[kind]}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {workflows.map((one) => (
              <TableRow
                key={one.workflow}
                sx={{ minHeight: ROW_HEIGHT.default }}
              >
                <TableCell
                  sx={{
                    typography: "s1",
                    whiteSpace: "normal",
                    wordBreak: "break-word",
                  }}
                >
                  <Stack direction="row" spacing={0.75} alignItems="center">
                    {!one.missing?.length && (
                      <StatusBadge
                        status="supported"
                        label=""
                        variant="dot"
                        aria-label={COPY.complete}
                        sx={{ gap: 0 }}
                      />
                    )}
                    <span>{one.workflow}</span>
                  </Stack>
                </TableCell>
                {KINDS.map((kind) => (
                  <TableCell key={kind} sx={{ typography: "s1" }}>
                    {one[kind] > 0 ? (
                      one[kind]
                    ) : (
                      <StatusBadge
                        status="missing"
                        role="warning"
                        label={COPY.missing}
                        size="sm"
                      />
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}

WorkflowProof.propTypes = {
  // The coverage answer's `workflows`: one row per use case, or undefined while it loads.
  workflows: PropTypes.arrayOf(
    PropTypes.shape({
      workflow: PropTypes.string,
      success: PropTypes.number,
      refusal: PropTypes.number,
      wrong_result: PropTypes.number,
      missing: PropTypes.arrayOf(PropTypes.string),
    }),
  ),
  // Whether this environment has versioning; without it there is no saved default to count.
  versioned: PropTypes.bool,
};
