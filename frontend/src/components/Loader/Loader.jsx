import { Box } from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";

// Design System in-content loader: a single small centred spinner.
const Loader = () => {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: 100,
      }}
    >
      <ContentSkeleton variant="panel" />
    </Box>
  );
};

export default Loader;
