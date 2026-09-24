import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const DUMMY_TOOLTIP = "Placeholder — not provided by the current API";

// A column header the harness-jobs list cannot fill: the plain label plus a
// muted "dummy" pill. The label carries the DataGrid header class so it keeps
// the grid's own header styling (renderHeader skips GridColumnHeaderTitle).
export default function DummyHeaderLabel({ label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
      <span className="MuiDataGrid-columnHeaderTitle">{label}</span>
      <CustomTooltip show arrow size="small" title={DUMMY_TOOLTIP}>
        <Box
          component="span"
          sx={{
            px: 0.625,
            py: 0.125,
            borderRadius: 0.75,
            typography: "s3",
            fontWeight: "fontWeightSemiBold",
            color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
          }}
        >
          Dummy
        </Box>
      </CustomTooltip>
    </Stack>
  );
}

DummyHeaderLabel.propTypes = { label: PropTypes.string.isRequired };
