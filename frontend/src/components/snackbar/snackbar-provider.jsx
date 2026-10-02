import React from "react";
import { useRef } from "react";
import PropTypes from "prop-types";
import {
  closeSnackbar,
  SnackbarProvider as NotistackProvider,
} from "notistack";

import Slide from "@mui/material/Slide";
import IconButton from "@mui/material/IconButton";

import Iconify from "../iconify";
import { useSettingsContext } from "../settings";
import { StyledIcon, SNACKBAR_AUTO_HIDE_MS } from "./styles";
import ClampedContent from "./ClampedContent";

// ----------------------------------------------------------------------

export default function SnackbarProvider({ children, ...other }) {
  const settings = useSettingsContext();

  const isRTL = settings.themeDirection === "rtl";

  const notistackRef = useRef(null);

  return (
    <NotistackProvider
      ref={notistackRef}
      maxSnack={5}
      preventDuplicate
      autoHideDuration={SNACKBAR_AUTO_HIDE_MS}
      TransitionComponent={Slide}
      TransitionProps={{ direction: isRTL ? "right" : "left" }}
      variant="success"
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      iconVariant={{
        info: (
          <StyledIcon color="info">
            <Iconify icon="solar:info-circle-bold" width={18} />
          </StyledIcon>
        ),
        success: (
          <StyledIcon color="success">
            <Iconify icon="solar:check-circle-bold" width={18} />
          </StyledIcon>
        ),
        warning: (
          <StyledIcon color="warning">
            <Iconify icon="solar:danger-triangle-bold" width={18} />
          </StyledIcon>
        ),
        error: (
          <StyledIcon color="error">
            <Iconify icon="solar:danger-circle-bold" width={18} />
          </StyledIcon>
        ),
      }}
      Components={{
        default: ClampedContent,
        info: ClampedContent,
        success: ClampedContent,
        warning: ClampedContent,
        error: ClampedContent,
      }}
      action={(snackbarId) => (
        <IconButton
          size="small"
          onClick={() => closeSnackbar(snackbarId)}
          sx={{
            p: 0.5,
            ml: 0.5,
            color: "text.secondary",
            "&:hover": {
              color: "text.primary",
              backgroundColor: "action.hover",
            },
          }}
        >
          <Iconify width={18} icon="mingcute:close-line" />
        </IconButton>
      )}
      {...other}
    >
      {children}
    </NotistackProvider>
  );
}

SnackbarProvider.propTypes = {
  children: PropTypes.node,
};
