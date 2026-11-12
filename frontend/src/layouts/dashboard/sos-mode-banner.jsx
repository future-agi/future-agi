import React from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// Shown while a support user is signed in to a customer account (SOS mode).
// Shared by the dashboard layout and the full-width Omega Console layout.
export default function SosModeBanner() {
  if (!localStorage.getItem("sosMode")) return null;
  return (
    <Box
      sx={{
        position: "absolute",
        top: 0,
        left: "50%",
        transform: "translateX(-50%)",
        backgroundColor: "status.warning.solid",
        padding: 0.5,
        borderRadius: "4px",
        zIndex: 1000,
      }}
    >
      <Typography
        fontSize={12}
        fontWeight="fontWeightSemiBold"
        color="status.warning.onSolid"
      >
        🚨 You are in SOS Mode, and have access to user account please be
        careful and avoid making any drastic changes 🚨
      </Typography>
    </Box>
  );
}
