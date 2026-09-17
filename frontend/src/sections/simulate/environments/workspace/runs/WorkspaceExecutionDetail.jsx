import { Box } from "@mui/material";
import TestRunDetailView from "src/sections/test-detail/TestRunDetailView";

// The reused product execution detail, mounted inside the workspace Runs tab.
// TestRunDetailView owns a `height: 100vh` root sized for its standalone page;
// the child selector here out-specifies it so the detail fills the Runs body
// instead of the viewport — no edit to the product file is needed for layout.
export default function WorkspaceExecutionDetail() {
  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        "& > *:first-of-type": { height: "100%" },
      }}
    >
      <TestRunDetailView />
    </Box>
  );
}
