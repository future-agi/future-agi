// Round 2 placeholder surfaces (W0a). Stub components of R2 lanes render one
// of these until their owner fills them, so a mounted button always opens
// something titled and closable (no dead controls). Owners replace their
// stub file; this module stays for whoever still needs a placeholder.
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

export const COMING_SOON = "Coming in this build";

/** Title row with a close (✕) button, shared by the drawer and the dialog. */
function StubHeader({ title, onClose }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      gap={1}
    >
      <Typography typography="m3" fontWeight="fontWeightSemiBold" noWrap>
        {title}
      </Typography>
      <IconButton size="small" aria-label="Close" onClick={onClose}>
        <Iconify icon="mdi:close" width={18} />
      </IconButton>
    </Stack>
  );
}

StubHeader.propTypes = {
  title: PropTypes.string.isRequired,
  onClose: PropTypes.func,
};

/**
 * A right-side drawer titled with the feature name and `Coming in this
 * build`; closes with Esc and ✕.
 * @param {{open: boolean, onClose: Function, title: string, width?: number}} props
 */
export function StubDrawer({ open, onClose, title, width = 480 }) {
  return (
    <Drawer
      anchor="right"
      open={Boolean(open)}
      onClose={onClose}
      PaperProps={{
        sx: { width: { xs: "100%", sm: width }, p: 2, gap: 1.5 },
      }}
    >
      <StubHeader title={title} onClose={onClose} />
      <Typography typography="s1" color="text.secondary">
        {COMING_SOON}
      </Typography>
    </Drawer>
  );
}

StubDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string.isRequired,
  width: PropTypes.number,
};

/**
 * A small dialog titled with the feature name and `Coming in this build`;
 * closes with Esc and ✕.
 */
export function StubDialog({ open, onClose, title }) {
  return (
    <Dialog open={Boolean(open)} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <StubHeader title={title} onClose={onClose} />
      </DialogTitle>
      <DialogContent sx={{ pb: 3 }}>
        <Typography typography="s1" color="text.secondary">
          {COMING_SOON}
        </Typography>
      </DialogContent>
    </Dialog>
  );
}

StubDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string.isRequired,
};

/** A page-body placeholder: the feature name and `Coming in this build`. */
export function StubPage({ title, children }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={0.5}
      sx={{ flex: 1, py: 8, px: 2, minHeight: 240 }}
    >
      <Typography typography="m3" fontWeight="fontWeightSemiBold">
        {title}
      </Typography>
      <Typography typography="s1" color="text.secondary">
        {COMING_SOON}
      </Typography>
      {children ? <Box sx={{ pt: 1 }}>{children}</Box> : null}
    </Stack>
  );
}

StubPage.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
};
