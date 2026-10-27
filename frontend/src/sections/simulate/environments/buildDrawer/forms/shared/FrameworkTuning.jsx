import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ROW_BOX_SX, T12 } from "../../drawerStyles";

/**
 * What improvements can change for a framework, as a short spec sheet: what
 * the improver edits, the structure the adapter reads, and whether its
 * production traces reach the Error Feed. Nothing for a framework we don't know.
 */
export default function FrameworkTuning({ tuning, structure }) {
  if (!tuning) return null;
  const rows = [
    ["Improvements edit", tuning.edits],
    ...(structure
      ? [
          [
            "Structure",
            `${structure} · read by the adapter, changed only with a written rationale`,
          ],
        ]
      : []),
    [
      "Production traces",
      tuning.traced
        ? "Through traceAI, into the Error Feed"
        : "traceAI doesn't instrument it. Failures come from simulations",
    ],
  ];
  return (
    <Box sx={ROW_BOX_SX} aria-label="What improvements can change">
      {rows.map(([label, value], i) => (
        <Stack
          key={label}
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.875,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ ...T12, color: "text.secondary", width: 128, flexShrink: 0 }}
          >
            {label}
          </Typography>
          <Typography sx={{ ...T12, flex: 1, minWidth: 0 }}>{value}</Typography>
        </Stack>
      ))}
    </Box>
  );
}
FrameworkTuning.propTypes = {
  tuning: PropTypes.shape({
    edits: PropTypes.string,
    traced: PropTypes.bool,
  }),
  structure: PropTypes.string,
};
