import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const DUMMY_TOOLTIP = "Placeholder — not provided by the current API";

// The muted grey "Dummy" pill shown in a summary column header the backend
// cannot fill yet (Tokens / Cost / Said-not-done / Mean-return). Sibling to the
// My-Environments DummyHeaderLabel; the cells below it read an em dash, never a
// fabricated number.
export default function ColumnDummyTag() {
  return (
    <CustomTooltip show arrow size="small" title={DUMMY_TOOLTIP}>
      <Box
        component="span"
        sx={{
          ml: 0.75,
          px: 0.625,
          py: 0.125,
          borderRadius: 0.75,
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: "text.secondary",
          bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
          flexShrink: 0,
        }}
      >
        Dummy
      </Box>
    </CustomTooltip>
  );
}
