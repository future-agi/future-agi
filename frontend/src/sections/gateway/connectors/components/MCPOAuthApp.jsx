import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useOrganization } from "src/contexts/OrganizationContext";
import { LEVELS } from "src/pages/dashboard/settings/UserManagementV2/constant";
import { DetailSection } from "../../components/frame";
import { clientSecretProblem, urlVariableProblems } from "../connect-request";
import { useMCPOAuthApp } from "../hooks/useMCPOAuthApp";

// Uses the product's existing theme typography, palette and spacing tokens.
// Registration is distinct from signing in: no connected claim after saving.
export default function MCPOAuthApp({ app, provider, canManage }) {
  const oauth = useMCPOAuthApp(app.id);
  const { orgLevel } = useOrganization();
  const canSignIn =
    canManage ||
    (typeof orgLevel === "number" &&
      orgLevel >= LEVELS.MEMBER &&
      oauth.data?.mode === "per_user");
  const [mode, setMode] = useState(
    provider.account_modes.includes("user") ? "per_user" : "shared",
  );
  const [registration, setRegistration] = useState("dcr");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [values, setValues] = useState({});
  const [notice, setNotice] = useState(null);
  const [discovery, setDiscovery] = useState(null);
  const [focusClientId, setFocusClientId] = useState(false);
  const [focusNotice, setFocusNotice] = useState(false);
  const clientIdRef = useRef(null);
  const noticeRef = useRef(null);
  const clientIdId = useId();
  const reasonId = useId();
  const configured = oauth.data?.configured;
  const vendor = app.vendor?.name || app.name;
  const docs = app.docs_url?.startsWith("https://") ? app.docs_url : null;
  // Where the admin registers this provider's app (the catalog's app
  // registration URL); empty until the catalog provides one.
  const helpUrl = provider.credential?.help_url?.startsWith("https://")
    ? provider.credential.help_url
    : null;
  // Only someone who can register the app needs the link, and only until it
  // is configured.
  const appSetup = canManage && !configured ? helpUrl : null;
  // Without dynamic registration the reason line under the registration
  // choice says what to do; the discovery alert does not repeat it.
  const noDcr = discovery?.dcr_supported === false;
  // While it is shown, the reason line describes the Client ID field ahead of
  // its helper text (an aria-describedby in inputProps replaces MUI's own).
  const clientIdInput = noDcr
    ? { "aria-describedby": `${reasonId} ${clientIdId}-helper-text` }
    : undefined;
  const variables = provider.url_variables || [];
  const url = provider.url_template.replace(/\{([^}]+)\}/g, (match, name) => {
    const value =
      values[name] || variables.find((v) => v.name === name)?.default;
    return value ? encodeURIComponent(value) : match;
  });
  const target = {
    provider: provider.id,
    discovery_url: url,
    url_variables: values,
  };
  const invalidVariables =
    Object.keys(urlVariableProblems(provider, values)).length > 0;
  const invalidManual =
    registration === "manual" &&
    (!clientId.trim() || Boolean(clientSecretProblem(secret)));
  // Every failure the panel reports is shown, with focus, where the admin is.
  const report = (next) => {
    setNotice(next);
    setFocusNotice(true);
  };
  // Returns the discovery result, or null after showing why discovery failed;
  // a Save that ran it also says that nothing was saved.
  const check = async (saving = false) => {
    setNotice(null);
    setDiscovery(null);
    try {
      const result = await oauth.request(target, true);
      setDiscovery(result);
      // Without dynamic registration the org must register its own client.
      if (result.dcr_supported === false) setRegistration("manual");
      return result;
    } catch {
      report({
        severity: "error",
        text: `${saving ? "Not saved. " : ""}Discovery failed. Check the MCP server URL, its resource metadata and PKCE S256 support.`,
      });
      return null;
    }
  };
  // The Client ID field mounts, enabled, once the steered save settles.
  useEffect(() => {
    if (!focusClientId || registration !== "manual" || oauth.pending) return;
    clientIdRef.current?.focus();
    setFocusClientId(false);
  }, [focusClientId, registration, oauth.pending]);
  // A reported failure takes focus once its notice is drawn.
  useEffect(() => {
    if (!focusNotice || !notice) return;
    noticeRef.current?.focus();
    setFocusNotice(false);
  }, [focusNotice, notice]);
  const save = async () => {
    if (!canManage || invalidVariables || invalidManual) return;
    // A DCR save needs dynamic registration: check first, and steer to a
    // manual client instead of sending a save the server would refuse.
    if (registration === "dcr" && !discovery) {
      const result = await check(true);
      if (!result) return;
      if (result.dcr_supported === false) {
        setFocusClientId(true);
        return;
      }
    }
    const body = { ...target, mode, registration };
    if (registration === "manual")
      Object.assign(body, { client_id: clientId, client_secret: secret });
    setSecret("");
    setNotice(null);
    try {
      const result = await oauth.request(body);
      if (result.gateway_synced === false)
        report({
          severity: "warning",
          text: "OAuth app saved. The gateway has not picked up the change yet; wait for synchronization before signing in.",
        });
    } catch {
      // Never retain an Axios error, request config, or server-echoed secret.
      // The secret stays cleared; this focused notice says to re-enter it.
      report({
        severity: "error",
        text: "OAuth app could not be saved. Check discovery and client settings, then re-enter the secret if using a manual client.",
      });
    }
  };
  const signIn = async () => {
    if (!canSignIn) return;
    setNotice(null);
    try {
      await oauth.signIn(oauth.data.mode);
    } catch {
      report({
        severity: "error",
        text: "Sign-in could not start. Check the authorization server and gateway configuration, then retry.",
      });
    }
  };
  return (
    <DetailSection title="Configure OAuth">
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {notice && (
          <Alert ref={noticeRef} tabIndex={-1} severity={notice.severity}>
            {notice.text}
          </Alert>
        )}
        {oauth.isPending && (
          <Typography typography="s2">Loading OAuth configuration…</Typography>
        )}
        {oauth.isError && (
          <Alert
            severity="error"
            action={<Button onClick={() => oauth.refetch()}>Retry</Button>}
          >
            OAuth configuration could not be loaded.
          </Alert>
        )}
        {oauth.data && (
          <>
            <Typography typography="s2" color="text.secondary">
              Register the app first, then sign in to an account. Only an Owner
              or Admin can change its configuration.
            </Typography>
            {(docs || appSetup) && (
              <Typography typography="s2">
                {docs && (
                  <Link href={docs} target="_blank" rel="noopener noreferrer">
                    Open {app.name} docs
                  </Link>
                )}
                {docs && appSetup && <span aria-hidden="true"> · </span>}
                {appSetup && (
                  <Link
                    href={appSetup}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Create your {vendor} OAuth app
                  </Link>
                )}
              </Typography>
            )}
            <TextField
              label="Redirect URI"
              size="small"
              value={oauth.data.redirect_uri || ""}
              InputProps={{ readOnly: true }}
              helperText="Register this exact callback with the authorization server for a manual client."
            />
            {configured ? (
              <>
                <Alert severity="success">
                  OAuth app configured.{" "}
                  {oauth.data.registration === "manual"
                    ? "Client secret stored; it is never shown again."
                    : "Dynamic client registration is available."}
                </Alert>
                <Typography typography="s2">
                  {oauth.data.mode === "shared"
                    ? "Shared account"
                    : "Per-user accounts"}
                  . Sign in separately to connect an account.
                </Typography>
                {canSignIn ? (
                  <Button
                    variant="contained"
                    color="primary"
                    disabled={
                      oauth.pending || oauth.data.gateway_synced === false
                    }
                    onClick={signIn}
                  >
                    Sign in to {app.name}
                  </Button>
                ) : (
                  <Typography typography="s2" color="text.secondary">
                    {oauth.data.mode === "shared"
                      ? "Only an organization Owner or Admin can sign in to a shared account."
                      : "Read-only access. An organization Member, Admin or Owner can sign in to their own account."}
                  </Typography>
                )}
                {canManage && (
                  <Typography typography="s2" color="text.secondary">
                    To change the provider, client or account mode, disconnect
                    this app and configure it again.
                  </Typography>
                )}
              </>
            ) : canManage ? (
              <>
                {variables.map((variable) => (
                  <TextField
                    key={variable.name}
                    label={variable.name}
                    helperText={variable.description}
                    size="small"
                    value={values[variable.name] ?? ""}
                    placeholder={variable.default || ""}
                    disabled={oauth.pending}
                    onChange={(event) => {
                      setValues({
                        ...values,
                        [variable.name]: event.target.value,
                      });
                      setDiscovery(null);
                    }}
                  />
                ))}
                <TextField
                  label="Discovery URL (MCP server)"
                  size="small"
                  value={url}
                  InputProps={{ readOnly: true }}
                  helperText="Uses this provider's MCP resource metadata to discover its authorization server. Fill in URL variables above when required."
                />
                <Box>
                  <Button
                    variant="outlined"
                    disabled={oauth.pending || invalidVariables}
                    onClick={() => check()}
                  >
                    Test discovery
                  </Button>
                </Box>
                {discovery && (
                  <Alert
                    severity={discovery.dcr_supported ? "success" : "info"}
                  >
                    Discovery passed. PKCE S256 supported.
                    {!noDcr &&
                      (discovery.dcr_supported
                        ? " Dynamic registration available."
                        : " Dynamic registration unavailable; use a manual client.")}
                  </Alert>
                )}
                <RadioGroup
                  aria-label="Account mode"
                  value={mode}
                  onChange={(event) => setMode(event.target.value)}
                >
                  {provider.account_modes.includes("user") && (
                    <FormControlLabel
                      value="per_user"
                      control={<Radio />}
                      label="Per-user accounts"
                      disabled={oauth.pending}
                    />
                  )}
                  {provider.account_modes.includes("agent") && (
                    <FormControlLabel
                      value="shared"
                      control={<Radio />}
                      label="Shared account"
                      disabled={oauth.pending}
                    />
                  )}
                </RadioGroup>
                <Typography typography="s2" color="text.secondary">
                  {mode === "shared"
                    ? "An Owner or Admin signs in one account for everyone allowed to use this app."
                    : "Each person signs in with their own account. Their credentials are never shared."}
                </Typography>
                <RadioGroup
                  aria-label="Client registration"
                  value={registration}
                  onChange={(event) => {
                    setRegistration(event.target.value);
                    setSecret("");
                  }}
                >
                  <FormControlLabel
                    value="dcr"
                    control={<Radio />}
                    label="Dynamic registration (DCR)"
                    disabled={
                      oauth.pending || discovery?.dcr_supported === false
                    }
                  />
                  <FormControlLabel
                    value="manual"
                    control={<Radio />}
                    label="Manual client"
                    disabled={oauth.pending}
                  />
                </RadioGroup>
                {noDcr && (
                  <Typography
                    id={reasonId}
                    typography="s2"
                    color="text.secondary"
                  >
                    {`${app.name} does not support dynamic registration. Use a manual client.`}
                  </Typography>
                )}
                {registration === "manual" && (
                  <>
                    <TextField
                      id={clientIdId}
                      label="Client ID"
                      size="small"
                      inputRef={clientIdRef}
                      value={clientId}
                      onChange={(event) => setClientId(event.target.value)}
                      disabled={oauth.pending}
                      autoComplete="off"
                      helperText={`From your ${vendor} OAuth app. Add the Redirect URI above to that app first.`}
                      inputProps={clientIdInput}
                    />
                    <TextField
                      label="Client secret"
                      size="small"
                      type="password"
                      value={secret}
                      onChange={(event) => setSecret(event.target.value)}
                      disabled={oauth.pending}
                      autoComplete="new-password"
                      helperText="Write-only. Encrypted in the vault and never returned."
                    />
                  </>
                )}
                <Button
                  variant="contained"
                  color="primary"
                  disabled={
                    oauth.pending ||
                    invalidVariables ||
                    invalidManual ||
                    (registration === "dcr" &&
                      discovery?.dcr_supported === false)
                  }
                  onClick={save}
                >
                  Save OAuth app
                </Button>
              </>
            ) : (
              <Typography typography="s2">
                Not configured. Ask an organization Owner or Admin to register
                the OAuth app.
              </Typography>
            )}
          </>
        )}
      </Box>
    </DetailSection>
  );
}
MCPOAuthApp.propTypes = {
  app: PropTypes.object.isRequired,
  provider: PropTypes.object.isRequired,
  canManage: PropTypes.bool.isRequired,
};
