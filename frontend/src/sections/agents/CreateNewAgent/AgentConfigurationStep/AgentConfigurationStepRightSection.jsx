import { Icon } from "@iconify/react";
import { Box, Button, Typography, Link, useTheme, alpha } from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import AgentVideoPlayerModal from "../AgentVideoPlayerModal";
import PropTypes from "prop-types";
import { useWatch } from "react-hook-form";
import { ShowComponent } from "src/components/show";
import {
  PROVIDER_CHOICES,
  PROVIDER_STEPS_MAPPER,
  isLiveKitProvider,
} from "../../constants";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import { HOST_API } from "src/config-global";

const AgentConfigurationStepRightSection = ({ control, getValues }) => {
  const [open, setOpen] = useState(false);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const content = {
    title: "How to create Agent Definition?",
    subtitle: "Get ready to supercharge your AI development",
    url: "https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a",
  };
  const selectedProvider = useWatch({
    control,
    name: "provider",
    defaultValue: getValues("provider"),
  });
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
            backgroundColor: "blue.o5",
            border: "1px solid",
            borderColor: "blue.200",
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
                background: alpha(theme.palette.common.black, 0.6),
                backdropFilter: "blur(1px)",
                WebkitBackdropFilter: "blur(4px)",
                boxShadow: `0 2px 8px ${alpha(theme.palette.common.black, 0.3)}`,
                transition: "all 0.2s ease-in-out",
                cursor: "pointer",
                "&:hover": {
                  transform: "translate(-50%, -50%) scale(1.05)",
                },
              }}
            >
              <Icon
                icon="weui:play-filled"
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
                typography="s1_2"
                fontWeight={"fontWeightMedium"}
                color={"primary.main"}
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
        <ShowComponent
          condition={Boolean(selectedProvider && selectedProvider !== "others")}
        >
          <Box
            sx={{
              padding: 2,
              backgroundColor: "background.default",
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box
              display="flex"
              alignItems="center"
              gap={1}
              mb={1}
              py={1}
              borderBottom={"1px solid"}
              borderColor={"divider"}
            >
              <Typography
                typography="s1_2"
                color="text.secondary"
                fontWeight={"fontWeightSemiBold"}
              >
                {isLiveKitProvider(selectedProvider)
                  ? "Steps to configure LiveKit"
                  : "Steps to find Assistant ID and API Key"}
              </Typography>
            </Box>
            <Box display="flex" flexDirection="column" gap={1}>
              {(PROVIDER_STEPS_MAPPER[selectedProvider] || []).map(
                (step, index) => (
                  <Typography
                    key={index}
                    typography="s1"
                    color="text.primary"
                    component="div"
                  >
                    {step.label}{" "}
                    {step.link && (
                      <Link
                        href={step.link}
                        color="blue.500"
                        target="_blank"
                        rel="noopener noreferrer"
                        fontWeight="fontWeightMedium"
                        sx={{ textDecoration: "underline" }}
                      >
                        {step.linkText}
                      </Link>
                    )}
                  </Typography>
                ),
              )}

              <ShowComponent
                condition={selectedProvider === PROVIDER_CHOICES.RETELL}
              >
                <Typography
                  component={"span"}
                  typography="s1"
                  color="text.primary"
                >
                  {`${PROVIDER_STEPS_MAPPER[selectedProvider]?.length + 1}. Please add `}
                  <Typography
                    typography="s1"
                    color="text.primary"
                    component={"span"}
                    onClick={() => {
                      copyToClipboard(`${HOST_API}/tracer/webhook`);
                      enqueueSnackbar({
                        message: "Copied to clipboard",
                        variant: "success",
                      });
                    }}
                    sx={{
                      textDecorationLine: "underline",
                      ":hover": {
                        cursor: "pointer",
                      },
                    }}
                  >
                    {`${HOST_API}/tracer/webhook`}
                  </Typography>
                  {` to the Agent Level Webhook URL on Retell`}
                </Typography>
              </ShowComponent>
            </Box>
          </Box>
        </ShowComponent>
      </Box>
      <AgentVideoPlayerModal
        open={open}
        onClose={() => setOpen(false)}
        content={content}
      />
    </>
  );
};

export default AgentConfigurationStepRightSection;

AgentConfigurationStepRightSection.propTypes = {
  control: PropTypes.object,
  getValues: PropTypes.func,
};
