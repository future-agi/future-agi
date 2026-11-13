import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";

/*
  The end of a fix made in builder chat (14 Nov): what the re-check found,
  and one way back to the run plan. The run itself is never started from
  here — it costs credits and the plan is where scenarios, repeats and
  limits are confirmed.
*/
export default function BuilderFixResult({ passed, left, onOpen }) {
  return (
    <Stack
      spacing={1}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        px: 1.5,
        py: 1.25,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box sx={{ pt: "7px", display: "flex", flexShrink: 0 }}>
          <StatusDot status={passed ? "passed" : "flaky"} size={6} />
        </Box>
        <Typography sx={{ typography: "s2" }}>
          {passed
            ? "The pair passed its check. Nothing left to fix before the run."
            : left.length
              ? `Fixed. ${left.length} still to fix in the run plan: ${left.join(", ")}.`
              : "Fixed. Open the run plan to check again."}
        </Typography>
      </Stack>
      <Button
        size="small"
        variant="contained"
        color="primary"
        onClick={onOpen}
        sx={{ alignSelf: "flex-start" }}
      >
        {passed ? "Review and start run" : "Open run plan"}
      </Button>
    </Stack>
  );
}
BuilderFixResult.propTypes = {
  passed: PropTypes.bool,
  left: PropTypes.array,
  onOpen: PropTypes.func.isRequired,
};
