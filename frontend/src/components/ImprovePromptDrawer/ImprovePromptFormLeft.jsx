import {
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "../svg-color";
import GeneratePromptForm from "../GeneratePromptDrawer/GeneratePromptForm";
import { LoadingButton } from "@mui/lab";

export default function ImprovePromptFormLeft({
  handleClose,
  followUpMessagesContainerRef,
  followUpPrompts,
  onSubmit,
  isImprovingPrompt,
  isValid,
  control,
}) {
  const theme = useTheme();

  return (
    <Box
      sx={{
        padding: theme.spacing(2),
        display: "flex",
        flexDirection: "column",
        rowGap: theme.spacing(2),
        paddingBottom: theme.spacing(0),
        width: "550px",
      }}
    >
      <Box
        sx={{
          width: "100%",
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
          flex: 1,
          height: "80vh",
        }}
      >
        <Stack
          direction={"row"}
          gap={theme.spacing(2)}
          alignItems={"flex-start"}
          justifyContent={"space-between"}
        >
          <Stack direction={"column"} gap={0}>
            <Typography
              typography="m3"
              fontWeight={"fontWeightSemiBold"}
              color={"text.primary"}
            >
              Improve a prompt
            </Typography>
            <Typography
              typography="s1"
              color="text.primary"
              fontWeight={"fontWeightRegular"}
            >
              Make changes to the prompt, then apply them on the workbench.
            </Typography>
          </Stack>
          <IconButton
            onClick={handleClose}
            sx={{
              margin: 0,
            }}
          >
            <SvgColor
              // @ts-ignore
              sx={{
                color: "text.primary",
                height: theme.spacing(2.5),
                width: theme.spacing(2.5),
              }}
              src="/assets/icons/ic_close.svg"
            />
          </IconButton>
        </Stack>
        {followUpPrompts?.length > 0 && (
          <Stack
            ref={followUpMessagesContainerRef}
            direction={"column"}
            gap={theme.spacing(2)}
            sx={{
              flex: 1,
              overflowY: "auto",
            }}
          >
            {followUpPrompts.map((prompt, index) => (
              <Box
                key={index}
                sx={{
                  padding: theme.spacing(1, 2),
                  backgroundColor: "background.neutral",
                  borderRadius: theme.spacing(0.5),
                }}
              >
                <Typography
                  typography="s1"
                  color={"text.primary"}
                  fontWeight={"fontWeightRegular"}
                >
                  {prompt}
                </Typography>
              </Box>
            ))}
          </Stack>
        )}
        <Box
          sx={{
            mt: followUpPrompts?.length > 0 ? "auto" : "unset",
          }}
        >
          <GeneratePromptForm
            onSubmit={onSubmit}
            disabled={isImprovingPrompt}
            control={control}
            placeholder={
              followUpPrompts?.length > 0
                ? "Make more changes"
                : "Describe your task..."
            }
            rows={followUpPrompts?.length > 0 ? 3 : 8}
          />
        </Box>
      </Box>
      <Stack
        direction={"row"}
        sx={{
          gap: theme.spacing(2),
          backgroundColor: "background.paper",
          // marginTop: theme.spacing(0),
          marginBottom: theme.spacing(2),
        }}
      >
        <Button variant="outlined" fullWidth onClick={handleClose}>
          Cancel
        </Button>
        <LoadingButton
          type="button"
          onClick={onSubmit}
          fullWidth
          loading={isImprovingPrompt}
          disabled={isImprovingPrompt || !isValid}
          variant="contained"
          color="primary"
        >
          Improve
        </LoadingButton>
      </Stack>
    </Box>
  );
}

ImprovePromptFormLeft.propTypes = {
  handleClose: PropTypes.func,
  handleHideShowImprovePrompt: PropTypes.func,
  hadnleShowImprovedPrompt: PropTypes.func,
  followUpMessagesContainerRef: PropTypes.object,
  followUpPrompts: PropTypes.array,
  onSubmit: PropTypes.func,
  control: PropTypes.object,
  isImprovingPrompt: PropTypes.bool,
  isValid: PropTypes.bool,
};
