import {
  Avatar,
  Box,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";
import Logo from "src/components/logo";
import SvgColor from "src/components/svg-color";
// import { Brain, FileText, Database, Infinity } from '@mui/icons-material';

const LeftSection = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        flex: 1,
        bgcolor: "background.neutral",
        height: "100vh",
        position: "relative",
        background: "url('/assets/illustrations/auth-background.png')",
      }}
    >
      <Box sx={{ paddingTop: "120px", paddingLeft: "90px" }}>
        <Logo width={185} height={43} sx={{ width: "185px", height: "43px" }} />
        <Box height="15px" />
        <Typography fontWeight={600} fontSize="24px">
          Sign-up to get world&apos;s most advanced
        </Typography>
        <Typography fontWeight={600} fontSize="24px">
          <Box
            component="span"
            sx={{
              background:
                theme.palette.mode === "light"
                  ? "linear-gradient(84deg, #1494F1 0%, #CF6BE8 50%, #9747FF 100%)"
                  : "linear-gradient(84deg, #FFFFFF 0%, #E6E6E7 50%, #E6E6E7 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            AI-lifecycle management
          </Box>{" "}
          system
        </Typography>
        <Box height="33px" />
        <List>
          {[
            {
              icon: <SvgColor src="/assets/icons/auth/ic_bulb_brain.svg" />,
              primary: "Exponentially better than Manual process:",
              secondary:
                "Critique AI agent saves 95% of human effort, time and cost",
            },
            {
              icon: <SvgColor src="/assets/icons/auth/ic_llm_judge.svg" />,
              primary: "State-of-art as compared to LLM-as-a-judge:",
              secondary: "Open source solutions are not working for most teams",
            },
            {
              icon: <SvgColor src="/assets/icons/auth/ic_scale_up.svg" />,
              primary: "Highly Scalable for enterprise grade apps:",
              secondary:
                "Can handle billions of inferences instead of just a sample",
            },
            {
              icon: <SvgColor src="/assets/icons/auth/ic_close_loop.svg" />,
              primary: "Not just Insights, but action to improve the model:",
              secondary:
                "Close the loop by getting correct data & prompt optimiser",
            },
          ].map((item, index) => (
            <ListItem
              key={index}
              alignItems="flex-start"
              sx={{ mb: 2, paddingLeft: 0 }}
            >
              <ListItemIcon sx={{}}>
                <Avatar>{item.icon}</Avatar>
              </ListItemIcon>
              <ListItemText
                primary={
                  <Typography fontWeight={600} fontSize="20px">
                    {item.primary}
                  </Typography>
                }
                secondary={
                  <Typography fontSize="13px">{item.secondary}</Typography>
                }
              />
            </ListItem>
          ))}
        </List>
      </Box>
    </Box>
  );
};

export default LeftSection;
