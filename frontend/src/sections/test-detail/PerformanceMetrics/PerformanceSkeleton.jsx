import React from "react";
import { Box, Grid, Skeleton, Typography } from "@mui/material";
import Section from "src/components/section";

const PerformanceSkeleton = () => {
  return (
    <Grid container spacing={2}>
      {/* --- Call details --- */}
      <Grid item xs={12} md={2}>
        <Section variant="panel" sx={{ p: 2 }}>
          <Typography
            typography="s2_1"
            color="text.primary"
            fontWeight={"fontWeightMedium"}
          >
            Call details
          </Typography>

          <Box sx={{ display: "flex", alignItems: "center", gap: 2, my: 2 }}>
            <Skeleton variant="circular" width={40} height={40} />
            <Box>
              <Skeleton width={30} height={24} />
              <Skeleton width={100} height={16} />
            </Box>
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
            <Skeleton variant="circular" width={40} height={40} />
            <Box>
              <Skeleton width={30} height={24} />
              <Skeleton width={100} height={16} />
            </Box>
          </Box>
        </Section>
      </Grid>

      {/* --- SYSTEM METRICS --- */}
      <Grid item xs={12} md={4}>
        <Section variant="panel" sx={{ p: 2 }}>
          <Typography
            typography="s2_1"
            color="text.primary"
            fontWeight={"fontWeightMedium"}
          >
            System metrics
          </Typography>

          <Grid
            container
            spacing={2}
            sx={{
              my: 2,
            }}
          >
            {[...Array(4)].map((_, i) => (
              <Grid item xs={6} key={i}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Skeleton variant="rounded" width={40} height={40} />
                  <Box>
                    <Skeleton width={80} height={16} />
                    <Skeleton width={50} height={24} />
                  </Box>
                </Box>
              </Grid>
            ))}
          </Grid>
        </Section>
      </Grid>

      {/* --- EVALUATION METRICS --- */}
      <Grid item xs={12} md={6}>
        <Section variant="panel" sx={{ p: 2 }}>
          <Typography
            typography="s2_1"
            color="text.primary"
            fontWeight={"fontWeightMedium"}
          >
            Evaluation metrics
          </Typography>

          <Box
            sx={{
              my: 2,
            }}
          />
          {[...Array(3)].map((_, i) => (
            <Box key={i} sx={{ mb: 2 }}>
              <Skeleton width={120} height={16} sx={{ mb: 1 }} />
              <Skeleton variant="rectangular" height={10} width="100%" />
            </Box>
          ))}
        </Section>
      </Grid>
    </Grid>
  );
};

export default PerformanceSkeleton;
