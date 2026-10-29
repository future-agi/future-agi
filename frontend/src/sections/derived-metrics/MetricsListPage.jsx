import { Box, Typography } from "@mui/material";

// Route shell; F03 supplies the list after the data-layer and contract gates.
export default function MetricsListPage() {
  return (
    <Box sx={{ p: 2, height: "100%", bgcolor: "background.paper" }}>
      <Typography component="h1" variant="h6" fontWeight={600}>
        Metrics
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        The metrics list is not available in this build yet.
      </Typography>
    </Box>
  );
}
