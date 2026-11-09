import React from "react";
import { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  closeSnackbar,
  SnackbarProvider as NotistackProvider,
} from "notistack";

import Slide from "@mui/material/Slide";
import IconButton from "@mui/material/IconButton";
import GlobalStyles from "@mui/material/GlobalStyles";

import Iconify from "../iconify";
import { useSettingsContext } from "../settings";
import { StyledIcon, SNACKBAR_AUTO_HIDE_MS } from "./styles";
import ClampedContent from "./ClampedContent";
import {
  syncToastPlacement,
  TOAST_HIDDEN_CLASS,
  TOAST_LEFT_VAR,
  TOAST_LIFT_VAR,
  TOAST_MAXW_VAR,
} from "./toastPlacement";

/** Class of the bottom-left container (placed by toastPlacement vars). */
const CONTAINER_CLASS = "fa-toasts";

// ----------------------------------------------------------------------

export default function SnackbarProvider({ children, ...other }) {
  const settings = useSettingsContext();

  const isRTL = settings.themeDirection === "rtl";

  const notistackRef = useRef(null);

  useEffect(() => {
    window.addEventListener("resize", syncToastPlacement);
    return () => window.removeEventListener("resize", syncToastPlacement);
  }, []);

  // IT-R1 G2 P8 (was R3 G0 bottom-center): bottom-left of the page content
  // (`<main>`), lifted over any bar at the bottom (toastPlacement.js), so a
  // toast never covers the selection / pending / verdict bars, page
  // headers, right drawers or the bottom-right Falcon bubble. At most 2,
  // 4 s, identical messages deduplicated (`notify` also replaces by key).
  // `disableWindowBlurListener`: the timer runs while the window is not
  // focused (it paused forever in an unfocused window before).
  return (
    <>
      <GlobalStyles
        styles={{
          ".notistack-SnackbarContainer .notistack-CollapseWrapper": {
            paddingTop: "4px !important",
            paddingBottom: "4px !important",
          },
          ".notistack-SnackbarContainer": {
            marginBottom: `max(var(--fa-toast-offset, 0px), var(${TOAST_LIFT_VAR}, 0px))`,
          },
          [`@media (min-width: 600px)`]: {
            [`.${CONTAINER_CLASS}`]: {
              left: `var(${TOAST_LEFT_VAR}, 20px) !important`,
              // IT-R2 G2 P10: never wider than the room left of an open
              // drawer or dialog; no toast when that room is too narrow.
              maxWidth: `min(420px, calc(100vw - 32px), var(${TOAST_MAXW_VAR}, 420px))`,
            },
          },
          // IT-R5 G2 S2: a URL in a toast is one line with an ellipsis
          // (the clipboard holds all of it); the message box may shrink.
          [`.${CONTAINER_CLASS} #notistack-snackbar`]: {
            minWidth: 0,
            flex: "1 1 auto",
          },
          [`.${CONTAINER_CLASS} #notistack-snackbar > *`]: {
            minWidth: 0,
            maxWidth: "100%",
          },
          [`.${CONTAINER_CLASS} [data-toast-url], .${CONTAINER_CLASS} [data-testid="share-link-url"]`]:
            {
              whiteSpace: "nowrap !important",
              overflow: "hidden !important",
              textOverflow: "ellipsis",
              wordBreak: "normal !important",
              maxHeight: "none !important",
              maxWidth: "100%",
            },
          [`body.${TOAST_HIDDEN_CLASS} .${CONTAINER_CLASS}`]: {
            visibility: "hidden",
          },
        }}
      />
      <NotistackProvider
        ref={notistackRef}
        maxSnack={2}
        preventDuplicate
        disableWindowBlurListener
        autoHideDuration={SNACKBAR_AUTO_HIDE_MS}
        TransitionComponent={Slide}
        TransitionProps={{ direction: isRTL ? "down" : "up" }}
        variant="success"
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        classes={{ containerAnchorOriginBottomLeft: CONTAINER_CLASS }}
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
    </>
  );
}

SnackbarProvider.propTypes = {
  children: PropTypes.node,
};
