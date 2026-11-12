import { Box } from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";

// Loading overlay component
const LoadingOverlay = () => (
  <Box
    sx={{
      position: "absolute",
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 1000,
    }}
  >
    <Box sx={{ textAlign: "center" }}>
      <ContentSkeleton variant="panel" />
    </Box>
  </Box>
);

export default LoadingOverlay;
