import React from "react";
import { Skeleton, Box, Grid } from "@mui/material";

const SessionSkeleton = () => {
  return (
    <Grid
      container
      spacing={0}
      sx={{
        marginTop: 2,
        marginBottom: "35px",
        border: "1px solid",
        borderRadius: "8px",
        borderColor: "text.disabled",
      }}
    >
      {/* Left Section */}
      <Grid
        item
        xs={9}
        sx={{
          minHeight: "440px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          borderTopLeftRadius: "8px",
          borderBottomLeftRadius: "8px",
          position: "relative",
          backgroundColor: "action.hover",
          paddingY: 4,
          borderRight: "1px solid",
          borderColor: "divider",
        }}
      >
        {/* <Chip
          label="Trace"
          sx={{
            position: "absolute",
            top: -15,
            left: 15,
            zIndex: 10,
            backgroundColor: "text.primary",
            color: "common.white",
            borderColor: "text.primary",
            borderWidth: 1,
            "&:hover": {
              backgroundColor: "text.primary",
            },
          }}
        /> */}
        <Box sx={{ padding: 2, display: "flex", flexDirection: "column" }}>
          <Skeleton
            variant="text"
            width="70%"
            height={150}
            sx={{ alignSelf: "flex-start" }}
          />
          <Skeleton
            variant="text"
            width="70%"
            height={200}
            sx={{ alignSelf: "flex-end" }}
          />
        </Box>
      </Grid>

      {/* Right Section */}
      <Grid item xs={3}>
        <Box sx={{ padding: 2, display: "flex", flexDirection: "column" }}>
          <Skeleton variant="text" width="100%" height={100} />
          <Skeleton variant="text" width="100%" height={100} />
          <Skeleton variant="text" width="100%" height={100} />
        </Box>
      </Grid>
    </Grid>
  );
};

export default SessionSkeleton;
