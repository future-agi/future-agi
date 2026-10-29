import { Box, Typography } from "@mui/material";
import { useParams } from "react-router-dom";

// Route shell; F05 supplies the create/edit builder and its navigation guard.
export default function MetricBuilderPage() {
  const { metricId } = useParams();

  return (
    <Box sx={{ p: 2, height: "100%", bgcolor: "background.paper" }}>
      <Typography component="h1" variant="h6" fontWeight={600}>
        {metricId ? "Edit metric" : "New metric"}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        The metric editor is not available in this build yet.
      </Typography>
    </Box>
  );
}
