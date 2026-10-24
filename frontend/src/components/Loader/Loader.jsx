import { Box, CircularProgress } from "@mui/material";

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
      <CircularProgress size={20} />
    </Box>
  );
};

export default Loader;
