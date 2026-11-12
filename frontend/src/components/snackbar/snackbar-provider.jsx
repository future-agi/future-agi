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
  TOAST_LIFT_VAR,
  TOAST_MAXW_VAR,
  TOAST_RIGHT_VAR,
  TOAST_Z_INDEX,
} from "./toastPlacement";

/** Class of the bottom-right container (placed by toastPlacement vars). */
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

  // Toasts sit bottom-right of the page content (`<main>`), placed by
  // toastPlacement.js: lifted over the selection / pending / verdict bars,
  // grid footers, the Falcon bubble and the run tray chip; moved left of an
  // open right drawer or dialog, or shown over it (above it, clear of its
  // buttons) when the room left beside it is under MIN_ROOM_PX. Placement
  // never hides a toast. At most 2, 4 s, identical messages deduplicated
  // (`notify` also replaces by key).
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
          // Above every drawer, dialog and backdrop, so a toast shown over
          // one is on top of it.
          [`.${CONTAINER_CLASS}`]: {
            zIndex: `${TOAST_Z_INDEX} !important`,
          },
          [`@media (min-width: 600px)`]: {
            [`.${CONTAINER_CLASS}`]: {
              // The right inset is measured (the content's right edge
              // − 16 px, left of an open drawer / dialog, or inside one).
              right: `var(${TOAST_RIGHT_VAR}, 20px) !important`,
              // Never wider than the room left of an open drawer or
              // dialog, or than the drawer or dialog it shows over.
              maxWidth: `min(420px, calc(100vw - 32px), var(${TOAST_MAXW_VAR}, 420px))`,
            },
            // A toast's own 320 px minimum gives way to a narrower column.
            [`.${CONTAINER_CLASS} .notistack-MuiContent`]: {
              minWidth: `min(320px, var(${TOAST_MAXW_VAR}, 320px)) !important`,
            },
          },
          // A URL in a toast is one line with an ellipsis (the clipboard
          // holds all of it); the message box may shrink.
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
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        classes={{ containerAnchorOriginBottomRight: CONTAINER_CLASS }}
        iconVariant={{
          info: (
            <StyledIcon color="info">
              <Iconify icon="solar:info-circle-bold" width={20} />
            </StyledIcon>
          ),
          success: (
            <StyledIcon color="success">
              <Iconify icon="solar:check-circle-bold" width={20} />
            </StyledIcon>
          ),
          warning: (
            <StyledIcon color="warning">
              <Iconify icon="solar:danger-triangle-bold" width={20} />
            </StyledIcon>
          ),
          error: (
            <StyledIcon color="error">
              <Iconify icon="solar:danger-circle-bold" width={20} />
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
            aria-label="Dismiss notification"
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
            <Iconify width={20} icon="solar:close-linear" />
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
