import { Helmet } from "react-helmet-async";
import { Box, Typography } from "@mui/material";

export default function EnvironmentDetailPage() {
  return (
    <>
      <Helmet>
        <title>Environment | Future AGI</title>
      </Helmet>
      <Box
        sx={{
          height: "100%",
          display: "grid",
          placeItems: "center",
          p: 4,
          textAlign: "center",
        }}
      >
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          This environment&apos;s page lands in a later phase.
        </Typography>
      </Box>
    </>
  );
}
