import { Box, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useModelRoutes } from "./useModelRoutes";

// Opens Gateway, API keys with the Create key dialog open.
const CREATE_GATEWAY_KEY_PATH = `${paths.dashboard.gateway.keys}?create=1`;

const OWN_KEY_USE =
  "prompts, datasets, experiments and evaluations use its models.";
// Calls through the organization's own provider routes on the gateway are
// not paid from credits, and the key is for the person's own code.
const CREDITS_USE =
  "calls your code makes with it to Future AGI models are paid from your Future AGI credits.";

const Line = ({ children }) => (
  <Typography
    component="li"
    variant="s2"
    fontWeight={"fontWeightRegular"}
    color="text.secondary"
  >
    {children}
  </Typography>
);
Line.propTypes = { children: PropTypes.node };

// The two ways to call a model, for an organization with Future AGI credits
// on: its own provider key, or a Gateway API key paid from credits. On the
// Add keys step (`inKeyCards`) it shows whenever credits are on; on a step
// that calls a model it shows only when no provider key or custom model is
// saved. With credits off or unknown it shows nothing, as before.
const ModelRoutes = ({ sentence, onAddProviderKey, inKeyCards = false }) => {
  const { creditsOn, noOwnModel, canAddProviderKey, canCreateGatewayKey } =
    useModelRoutes({ ownModelNeeded: !inKeyCards });
  if (!creditsOn || (!inKeyCards && !noOwnModel)) return null;

  let ownKey;
  if (!canAddProviderKey) {
    ownKey = `Ask an organization admin to add a provider key: ${OWN_KEY_USE}`;
  } else if (inKeyCards) {
    ownKey = "Use your own provider key: add it in the cards below.";
  } else {
    ownKey = (
      <>
        <Link
          component="button"
          type="button"
          variant="s2"
          fontWeight={"fontWeightMedium"}
          onClick={onAddProviderKey}
          sx={{ verticalAlign: "baseline" }}
        >
          Add a provider key
        </Link>
        {`: ${OWN_KEY_USE}`}
      </>
    );
  }

  const credits = canCreateGatewayKey ? (
    <>
      <Link
        component={RouterLink}
        href={CREATE_GATEWAY_KEY_PATH}
        fontWeight={"fontWeightMedium"}
      >
        Create a Gateway API key
      </Link>
      {`: ${CREDITS_USE}`}
    </>
  ) : (
    `Ask an organization admin for a Gateway API key: ${CREDITS_USE}`
  );

  return (
    <Box
      component="section"
      aria-label="Ways to call a model"
      sx={{
        mt: "12px",
        p: "12px",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        backgroundColor: "background.neutral",
        display: "flex",
        flexDirection: "column",
        gap: "6px",
      }}
    >
      <Typography
        variant="s2"
        fontWeight={"fontWeightMedium"}
        color="text.primary"
      >
        {sentence}
      </Typography>
      <Box
        component="ul"
        sx={{
          m: 0,
          pl: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "4px",
        }}
      >
        <Line>{ownKey}</Line>
        <Line>{credits}</Line>
      </Box>
    </Box>
  );
};

ModelRoutes.propTypes = {
  sentence: PropTypes.string.isRequired,
  onAddProviderKey: PropTypes.func,
  inKeyCards: PropTypes.bool,
};

export default ModelRoutes;
