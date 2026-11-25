import { Icon } from "@iconify/react";
import { Box, Button, Typography, useTheme } from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import AgentVideoPlayerModal from "../AgentVideoPlayerModal";

const AgentBasicInfoStepRightSection = () => {
  const [open, setOpen] = useState(false);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const content = {
    title: "How to create agent definition",
    subtitle: "Get ready to supercharge your AI development",
    url: "https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a",
  };
  return (
    <>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography typography={"m3"} fontWeight={"fontWeightMedium"}>
          Resources
        </Typography>
        <Box
          sx={{
            padding: "12px",
            display: "flex",
            gap: "12px",
            backgroundColor: "status.info.bg",
            border: "1px solid",
            borderColor: "status.info.border",
            borderRadius: "4px",
          }}
        >
          <Box
            sx={{
              width: "160px",
              height: "90px",
              borderRadius: "4px",
              position: "relative",
            }}
            onClick={() => setOpen(true)}
          >
            <Box
              sx={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 35,
                height: 35,
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(0, 0, 0, 0.6)",
                backdropFilter: "blur(1px)",
                WebkitBackdropFilter: "blur(4px)",
                cursor: "pointer",
              }}
            >
              <Icon
                icon="solar:play-linear"
                width={22}
                height={22}
                color="white"
                style={{ pointerEvents: "none" }}
              />
            </Box>

            <img
              src={
                isDark
                  ? "/assets/synthetic-data-thumbnail_dark.png"
                  : "/assets/synthetic-data-thumbnail.png"
              }
              width={"158px"}
              height={"88px"}
              style={{
                borderRadius: "4px",
              }}
            />
          </Box>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <Typography
              typography="s1"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              How to create Agent Definition?
            </Typography>
            <Typography
              typography="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              A configuration that specifies how your AI agent behaves during
              voice/chat conversations
            </Typography>
            <Button
              size="small"
              sx={{
                marginTop: "12px",
                borderColor: "primary.main",
                width: "max-content",
                color: "primary.main",
              }}
              onClick={() => setOpen(true)}
            >
              <SvgColor
                src={"/assets/icons/agent/play_button.svg"}
                sx={{
                  height: 20,
                  width: 20,
                }}
              />
              <Typography
                typography="m3"
                fontWeight={"fontWeightMedium"}
                sx={{
                  textDecoration: "underline",
                  marginLeft: "4px",
                }}
              >
                Play video
              </Typography>
            </Button>
          </Box>
        </Box>
      </Box>
      <AgentVideoPlayerModal
        open={open}
        onClose={() => setOpen(false)}
        content={content}
      />
    </>
  );
};

export default AgentBasicInfoStepRightSection;
