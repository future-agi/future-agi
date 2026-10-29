import { Box, Typography } from "@mui/material";

// Route shell; F04 supplies Overview and the History/Definition tab navigation.
export default function MetricDetailPage() {
  return (
    <Box sx={{ p: 2, height: "100%", bgcolor: "background.paper" }}>
      <Typography component="h1" variant="h6" fontWeight={600}>
        Metric
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        Metric details are not available in this build yet.
      </Typography>
    </Box>
  );
}
