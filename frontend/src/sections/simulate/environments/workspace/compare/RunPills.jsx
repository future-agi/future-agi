import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { agentText, passText } from "./compareFormat";

// A run's number on its identity colour, as the runs list draws it.
export function RunLetter({ letter, color }) {
  return (
    <Box
      sx={{
        minWidth: 20,
        height: 20,
        px: 0.25,
        borderRadius: "4px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        color,
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
RunLetter.propTypes = { letter: PropTypes.string, color: PropTypes.string };

/**
 * The compared runs, baseline first. Each pill names the run, its agent
 * version and its pass rate; with more than two runs, any can be dropped.
 */
export default function RunPills({ runs, onRemove }) {
  const removable = runs.length > 2;
  return runs.map((run, i) => (
    <Stack
      key={run.id}
      data-testid="compare-run-pill"
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{
        pl: 1,
        pr: removable ? 0.25 : 1,
        py: 0.5,
        borderRadius: "6px",
        border: "1px solid",
        borderColor: alpha(run.color, 0.35),
        bgcolor: (t) =>
          alpha(run.color, t.palette.mode === "dark" ? 0.12 : 0.06),
      }}
    >
      <RunLetter letter={run.letter} color={run.color} />
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          maxWidth: 280,
        }}
      >
        {`${run.label} · ${agentText(run)} · ${passText(run)}`}
      </Typography>
      {i === 0 && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          baseline
        </Typography>
      )}
      {removable && (
        <CustomTooltip
          show
          arrow
          size="small"
          title="Remove from this comparison"
        >
          <IconButton
            size="small"
            aria-label="Remove from this comparison"
            onClick={() => onRemove(run.id)}
          >
            <Iconify
              icon="solar:close-linear"
              width={12}
              sx={{ color: "text.subtitle" }}
            />
          </IconButton>
        </CustomTooltip>
      )}
    </Stack>
  ));
}
RunPills.propTypes = {
  runs: PropTypes.array.isRequired,
  onRemove: PropTypes.func.isRequired,
};
