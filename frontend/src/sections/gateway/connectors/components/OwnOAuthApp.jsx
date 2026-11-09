import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import ConfirmDialog from "src/sections/gateway/components/ConfirmDialog";

import { DetailRow } from "../../components/frame";
import { clientSecretProblem, refusalText } from "../connect-request";
import {
  useRemoveOwnOAuthClient,
  useStoreOwnOAuthClient,
} from "../hooks/useOwnOAuthApp";

// "Use your own OAuth app" (CN11) for a sign-in app, for an Owner or Admin:
// the redirect URI to register with the vendor, exactly as the API names it,
// and the org's client ID and secret, which the gateway then signs people in
// with. The secret field is uncontrolled: the secret is never in React state
// or a DOM attribute, is read once on Save and cleared at once, and is never
// shown again (the API returns none). A stored client can be removed.
export default function OwnOAuthApp({ app, ownApp }) {
  const headingId = useId();
  const { offer, routes, current } = ownApp;
  const [clientId, setClientId] = useState("");
  const [problems, setProblems] = useState({ clientId: null, secret: null });
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const secretRef = useRef(null);
  const { store, isPending } = useStoreOwnOAuthClient();
  const remove = useRemoveOwnOAuthClient();
  const vendor = app.vendor?.name || app.name;

  const save = async (event) => {
    event.preventDefault();
    setError(null);
    const typed = secretRef.current?.value ?? "";
    if (secretRef.current) secretRef.current.value = "";
    const found = {
      clientId: clientId.trim() ? null : "Enter the client ID.",
      secret: clientSecretProblem(typed),
    };
    setProblems(found);
    if (found.clientId || found.secret) return;
    try {
      await store(routes.list, offer.clientGroup, clientId, typed);
    } catch (err) {
      setError(refusalText(err, typed));
      return;
    }
    setClientId("");
    enqueueSnackbar(`${app.name} signs in through your OAuth app.`, {
      variant: "success",
    });
  };

  const removeClient = async () => {
    try {
      await remove.mutateAsync({ url: routes.detail(current.id) });
      setConfirming(false);
      enqueueSnackbar(`Your OAuth app for ${app.name} is removed.`, {
        variant: "success",
      });
    } catch (err) {
      setConfirming(false);
      enqueueSnackbar(
        err?.message || `Your OAuth app for ${app.name} could not be removed.`,
        { variant: "error" },
      );
    }
  };

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Typography
        id={headingId}
        component="h3"
        typography="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        Your own OAuth app
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {`Register an OAuth app with ${vendor} using this redirect URI, then enter its client ID and secret. People then sign in to ${app.name} through your app.`}
      </Typography>
      <Box>
        <DetailRow label="Redirect URI" mono>
          {offer.redirectUri}
        </DetailRow>
        {current && (
          <DetailRow label="Client ID" mono>
            {current.client_id}
          </DetailRow>
        )}
      </Box>

      {current ? (
        <Box>
          <Button
            size="small"
            variant="outlined"
            color="error"
            onClick={() => setConfirming(true)}
          >
            Remove
          </Button>
        </Box>
      ) : (
        <Box
          component="form"
          noValidate
          onSubmit={save}
          sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
        >
          <TextField
            size="small"
            label="Client ID"
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            error={Boolean(problems.clientId)}
            helperText={problems.clientId || " "}
            inputProps={{ spellCheck: false, autoCapitalize: "off" }}
          />
          <TextField
            size="small"
            type="password"
            label="Client secret"
            inputRef={secretRef}
            defaultValue=""
            autoComplete="new-password"
            error={Boolean(problems.secret)}
            helperText={
              problems.secret || "Stored encrypted, and never shown again."
            }
            inputProps={{ spellCheck: false, autoCapitalize: "off" }}
          />
          {error && <Alert severity="error">{error}</Alert>}
          <Box>
            <Button
              type="submit"
              variant="contained"
              color="primary"
              size="small"
              disabled={isPending}
            >
              Save
            </Button>
          </Box>
        </Box>
      )}

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={removeClient}
        title={`Remove your OAuth app for ${app.name}?`}
        message={`The gateway stops using your OAuth app for new sign-ins to ${app.name}.`}
        confirmLabel="Remove"
        isLoading={remove.isPending}
      />
    </Box>
  );
}

OwnOAuthApp.propTypes = {
  app: PropTypes.shape({
    name: PropTypes.string.isRequired,
    vendor: PropTypes.shape({ name: PropTypes.string }),
  }).isRequired,
  ownApp: PropTypes.shape({
    offer: PropTypes.shape({
      clientGroup: PropTypes.string.isRequired,
      redirectUri: PropTypes.string.isRequired,
      required: PropTypes.bool,
    }).isRequired,
    routes: PropTypes.shape({
      list: PropTypes.string.isRequired,
      detail: PropTypes.func.isRequired,
    }).isRequired,
    current: PropTypes.shape({
      id: PropTypes.string.isRequired,
      client_id: PropTypes.string.isRequired,
    }),
  }).isRequired,
};
