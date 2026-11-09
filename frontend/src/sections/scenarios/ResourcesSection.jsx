import { Box, Typography } from "@mui/material";
import React, { useState } from "react";
import LoomDialog from "src/components/LoomDialog/LoomDialog";
import SvgColor from "src/components/svg-color";

const ResourcesSection = () => {
  const [openLoomDialog, setOpenLoomDialog] = useState(false);
  return (
    <Box sx={{ flex: 2, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography typography="m3" fontWeight={500}>
        Resources
      </Typography>
      <Box
        sx={{
          padding: 2,
          border: "1px solid",
          borderColor: "divider",
          backgroundColor: "background.neutral",
          borderRadius: 0.5,
          gap: "12px",
          display: "flex",
          alignItems: "center",
        }}
      >
        <Box
          sx={{
            borderRadius: 0.5,
            overflow: "hidden",
            backgroundColor: "background.neutral",
            maxWidth: "160px",
          }}
          onClick={() => setOpenLoomDialog(true)}
        >
          <img
            alt="Play external tutorial"
            width={96}
            height={64}
            src="/assets/icons/action_buttons/ic_filled_play_button.svg"
          />
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            gap: 1,
            justifyContent: "center",
          }}
        >
          <Box>
            <Typography typography="s1" fontWeight="fontWeightMedium">
              How to create Scenarios?
            </Typography>
            <Typography typography="s2">
              Scenarios defines the test cases, customer profiles, and
              conversation flows that your AI agent will encounter during
              simulations.
            </Typography>
          </Box>
          <Box
            sx={{ display: "flex", alignItems: "center", gap: 1 }}
            onClick={() => setOpenLoomDialog(true)}
          >
            <SvgColor
              src="/assets/icons/action_buttons/ic_filled_play_button.svg"
              sx={{ color: "primary.main" }}
              width={20}
              height={20}
            />
            <Typography
              typography="s1_2"
              fontWeight="fontWeightMedium"
              color="primary.main"
              sx={{
                cursor: "pointer",
                textDecoration: "underline",
              }}
            >
              Play video
            </Typography>
          </Box>
        </Box>
      </Box>
      <LoomDialog
        open={openLoomDialog}
        onClose={() => setOpenLoomDialog(false)}
        title="How to create Scenarios?"
        loomUrl="https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a"
      />
    </Box>
  );
};

export default ResourcesSection;
