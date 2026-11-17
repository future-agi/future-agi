import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { COPY_ERROR_MESSAGE } from "../lib/copyText";
import { useAccessToken } from "../lib/useAccessToken";
import CopyField from "./CopyField";
import DeployIcon from "./DeployIcon";

/**
 * The deployment's access token, while this tab holds it (it is shown once,
 * in the reply that minted it). Studio and API clients of the served project
 * present it as a bearer token.
 */
export default function AccessTokenNotice({ id }) {
  const token = useAccessToken(id);
  if (!token) return null;
  return (
    <Box
      data-testid="access-token-notice"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <DeployIcon name="key" size={16} sx={{ color: "primary.main" }} />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Access token
        </Typography>
      </Box>
      <Typography typography="s2" color="text.secondary">
        Copy it now: it is shown only once. Studio and API clients of this
        deployment send it as a bearer token. You can rotate it under Settings.
      </Typography>
      <CopyField
        value={token}
        label="access token"
        secret
        testId="access-token"
        failureMessage={COPY_ERROR_MESSAGE}
      />
    </Box>
  );
}

AccessTokenNotice.propTypes = { id: PropTypes.string.isRequired };
