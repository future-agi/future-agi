import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Under a provider's name, when the server marks its stored key or settings as
// not valid: the server's sentence as it words it, in the warning tone, with a
// warning glyph so the state does not rest on colour. The sentence wraps in a
// narrow card and is never cut short. Nothing renders without a message.
const ProviderConfigurationNotice = ({ message }) => {
  if (!message) return null;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={0.75}
      data-testid="provider-configuration-notice"
      sx={{ color: "status.warning.fg", minWidth: 0 }}
    >
      <Iconify
        icon="solar:danger-triangle-linear"
        width={16}
        aria-hidden
        sx={{ flexShrink: 0, mt: 0.125 }}
      />
      <Typography
        typography="s1"
        color="inherit"
        sx={{ minWidth: 0, overflowWrap: "anywhere" }}
      >
        {message}
      </Typography>
    </Stack>
  );
};

ProviderConfigurationNotice.propTypes = {
  message: PropTypes.string,
};

export default ProviderConfigurationNotice;
