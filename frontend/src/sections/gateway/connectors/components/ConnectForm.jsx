import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormLabel from "@mui/material/FormLabel";
import Link from "@mui/material/Link";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { authLabel, isSignInCredential, placementText } from "../catalog-model";
import { accountModes, connectTarget, openSignIn } from "../connect-flow";
import {
  addRequestBody,
  keyProblem,
  needsKey,
  urlVariableProblems,
} from "../connect-request";
import {
  useAddConnector,
  useStoreConnectorKey,
} from "../hooks/useConnectorCatalog";
import { useStartConnect } from "../hooks/useMyConnections";

// A message from the API, never one that repeats the key.
function messageOf(error, typed) {
  const message =
    (typeof error?.message === "string" && error.message) ||
    (typeof error?.detail === "string" && error.detail) ||
    "Something went wrong. Try again.";
  return typed && message.includes(typed)
    ? "The server refused the request."
    : message;
}

function warnIfNotSynced(result, name) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(
      result.gateway_warning ||
        `${name} is saved, but the gateway has not picked up the change yet.`,
      { variant: "warning" },
    );
  }
}

// Connect one app. With no server yet ("add"): pick a provider when there
// are several, fill its URL variables and its key, then add it from the
// catalog with the key in one request (CN-ADD: the add lists the provider's
// tools with it and stores it in the vault in the same write, or adds
// nothing). With a server that has no key ("key"): store the key only. The
// key field is uncontrolled: the key is never in React state or a DOM
// attribute and is read once on submit. The field is cleared once the key
// is sent or refused; a valid key stays only while a URL variable is fixed,
// so it need not be pasted again.
//
// A sign-in provider takes no key: pick the account mode when the catalog
// offers both, then the add sends no credential, and the browser goes to the
// sign-in URL its answer names (connect-flow.js). With no such URL the app
// is added, and its drawer offers the sign-in the API names for it.
//
// The drawer owns which provider is picked (`provider`, changed through
// `onProvider`): with a server, only the provider it was added with; it is
// never swapped for another one. A provider configured as an MCP OAuth app
// is not added here (the API refuses it with oauth_configuration_required):
// with one picked, the form keeps only the provider choice, and the drawer
// shows that provider's OAuth app configuration.
export default function ConnectForm({
  app,
  provider,
  providers,
  onProvider,
  server,
  held = false,
}) {
  const step = server ? "key" : "add";
  const [values, setValues] = useState({});
  const [chosenMode, setChosenMode] = useState(null);
  const [problems, setProblems] = useState({ variables: {}, key: null });
  const [error, setError] = useState(null);
  const keyRef = useRef(null);
  const { add, isPending: adding } = useAddConnector();
  const { store, isPending: storing } = useStoreConnectorKey();
  const choiceLabel = useId();
  const modesLabel = useId();
  const { start, pending: starting } = useStartConnect();
  const pending = adding || storing || Boolean(starting);

  if (!provider) return null;
  const oauthApp = provider.connect?.configure_oauth === true;
  const credential = provider.credential;
  const takesKey = needsKey(credential);
  const signIn = isSignInCredential(credential);
  const modes = signIn ? accountModes(provider) : [];
  const mode = modes.find((m) => m.id === chosenMode) || modes[0] || null;

  const submit = async (event) => {
    event.preventDefault();
    if (held || pending || provider.connect?.state === "registration_required")
      return;
    setError(null);
    const typed = keyRef.current?.value ?? "";
    const variables =
      step === "add" ? urlVariableProblems(provider, values) : {};
    const key = takesKey ? keyProblem(typed) : null;
    const blocked = Object.keys(variables).length > 0 || key !== null;
    if (keyRef.current && (key !== null || !blocked)) {
      keyRef.current.value = "";
    }
    setProblems({ variables, key });
    if (blocked) return;

    let result;
    try {
      result =
        step === "add"
          ? await add(
              app.id,
              addRequestBody(
                provider,
                values,
                takesKey ? typed : "",
                mode?.id || null,
              ),
            )
          : takesKey
            ? await store(server.server_id, credential, typed)
            : null;
      warnIfNotSynced(result, app.name);
    } catch (err) {
      setError(messageOf(err, typed));
      return;
    }
    if (signIn) {
      if (result?.start_url) {
        try {
          await start(result);
        } catch (err) {
          setError(messageOf(err, ""));
        }
        return;
      }
      const target = connectTarget(result?.connect_url);
      if (target) {
        openSignIn(target);
        return;
      }
      enqueueSnackbar(`${app.name} is added.`, { variant: "success" });
      return;
    }
    enqueueSnackbar(`${app.name} is connected.`, { variant: "success" });
  };

  const choice = step === "add" && providers.length > 1 && (
    <Box>
      <FormLabel id={choiceLabel} sx={{ typography: "s2", fontWeight: 500 }}>
        Connect with
      </FormLabel>
      <RadioGroup
        aria-labelledby={choiceLabel}
        value={provider.id}
        onChange={(event) => {
          onProvider(event.target.value);
          setValues({});
          setChosenMode(null);
          setProblems({ variables: {}, key: null });
        }}
      >
        {providers.map((card) => (
          <FormControlLabel
            key={card.id}
            value={card.id}
            control={<Radio size="small" />}
            label={`${authLabel(card.credential)} · ${placementText(card.credential)}`}
          />
        ))}
      </RadioGroup>
    </Box>
  );

  if (oauthApp) return choice || null;

  return (
    <Box
      component="form"
      noValidate
      onSubmit={submit}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      {choice}

      {step === "add" &&
        (provider.url_variables || []).map((variable) => (
          <TextField
            key={`${provider.id}-${variable.name}`}
            size="small"
            label={variable.name}
            value={values[variable.name] ?? ""}
            placeholder={variable.default ?? ""}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                [variable.name]: event.target.value,
              }))
            }
            error={Boolean(problems.variables[variable.name])}
            helperText={
              problems.variables[variable.name] ||
              (variable.default != null
                ? `${variable.description} Leave empty for ${variable.default}.`
                : variable.description)
            }
            inputProps={{ spellCheck: false, autoCapitalize: "off" }}
          />
        ))}

      {signIn && modes.length > 1 && (
        <Box>
          <FormLabel id={modesLabel} sx={{ typography: "s2", fontWeight: 500 }}>
            Accounts
          </FormLabel>
          <RadioGroup
            aria-labelledby={modesLabel}
            value={mode.id}
            onChange={(event) => setChosenMode(event.target.value)}
          >
            {modes.map((option) => (
              <FormControlLabel
                key={option.id}
                value={option.id}
                control={<Radio size="small" />}
                label={option.label}
              />
            ))}
          </RadioGroup>
        </Box>
      )}

      {signIn ? (
        modes.length === 1 && (
          <Typography typography="s2" color="text.secondary">
            {mode.label}
          </Typography>
        )
      ) : takesKey ? (
        <Box>
          <TextField
            key={provider.id}
            fullWidth
            size="small"
            type="password"
            label={authLabel(credential)}
            inputRef={keyRef}
            defaultValue=""
            autoComplete="new-password"
            error={Boolean(problems.key)}
            helperText={problems.key || placementText(credential)}
            inputProps={{ spellCheck: false, autoCapitalize: "off" }}
          />
          {(credential.help || credential.help_url) && (
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ mt: 0.75 }}
            >
              {credential.help && <span>{credential.help}</span>}{" "}
              {credential.help_url?.startsWith("https://") && (
                <Link
                  href={credential.help_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Where to find it
                </Link>
              )}
            </Typography>
          )}
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary">
          {placementText(credential)}
        </Typography>
      )}

      {error && <Alert severity="error">{error}</Alert>}

      <Box>
        <Button
          type="submit"
          variant="contained"
          color="primary"
          size="small"
          disabled={
            pending ||
            held ||
            provider.connect?.state === "registration_required"
          }
        >
          {step === "add" ? "Connect" : "Save key"}
        </Button>
      </Box>
    </Box>
  );
}

ConnectForm.propTypes = {
  app: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    default_provider: PropTypes.string,
  }).isRequired,
  // The picked provider's card; the drawer owns the pick.
  provider: PropTypes.object,
  providers: PropTypes.array.isRequired,
  // Picks another provider by id (the choice shows before a server exists).
  onProvider: PropTypes.func.isRequired,
  server: PropTypes.shape({
    server_id: PropTypes.string.isRequired,
    provider: PropTypes.string,
  }),
  // Connect waits: the sign-in needs the org's own OAuth app first.
  held: PropTypes.bool,
};
