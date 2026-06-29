import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "../svg-color";
import ConfigureKeysContent from "./ConfigureKeysContent";

export default function ConfigureKeysModal({ open, onClose, selectedModel }) {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: "734px",
          minHeight: "400px",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle
        sx={{
          padding: 0,
          display: "flex",
          flexDirection: "row",
          alignItems: "flex-start",
          justifyContent: "space-between",
        }}
      >
        <Stack>
          <Typography
            color={"text.primary"}
            variant="m3"
            fontWeight={"fontWeightSemiBold"}
          >
            Configure API keys
          </Typography>
          <Typography
            color={"text.secondary"}
            variant="s1"
            fontWeight={"fontWeightBold"}
          >
            Configure your LLM API keys to run evals and prompts.
          </Typography>
        </Stack>
        <IconButton
          onClick={onClose}
          sx={{
            padding: theme.spacing(0.5),
            margin: 0,
          }}
        >
          <SvgColor
            sx={{
              color: "text.primary",
              height: theme.spacing(3),
              width: theme.spacing(3),
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
        }}
      >
        <ConfigureKeysContent
          selectedModel={selectedModel}
          shouldFetch={open}
          cols={1}
          gridSx={{
            overflowX: "auto",
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

ConfigureKeysModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedModel: PropTypes.object,
};
