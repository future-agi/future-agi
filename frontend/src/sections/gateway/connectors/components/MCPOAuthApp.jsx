import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
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
  const check = async () => {
    setNotice(null);
    setDiscovery(null);
    try {
      setDiscovery(await oauth.request(target, true));
    } catch {
      setNotice({
        severity: "error",
        text: "Discovery failed. Check the MCP server URL, its resource metadata and PKCE S256 support.",
      });
    }
  };
  const save = async () => {
    if (!canManage || invalidVariables || invalidManual) return;
    const body = { ...target, mode, registration };
    if (registration === "manual")
      Object.assign(body, { client_id: clientId, client_secret: secret });
    setSecret("");
    setNotice(null);
    try {
      const result = await oauth.request(body);
      if (result.gateway_synced === false)
        setNotice({
          severity: "warning",
          text: "OAuth app saved. The gateway has not picked up the change yet; wait for synchronization before signing in.",
        });
    } catch {
      // Never retain an Axios error, request config, or server-echoed secret.
      setNotice({
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
      setNotice({
        severity: "error",
        text: "Sign-in could not start. Check the authorization server and gateway configuration, then retry.",
      });
    }
  };
  const configured = oauth.data?.configured;
  return (
    <DetailSection title="Configure OAuth">
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {notice && <Alert severity={notice.severity}>{notice.text}</Alert>}
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
                    onClick={check}
                  >
                    Test discovery
                  </Button>
                </Box>
                {discovery && (
                  <Alert
                    severity={discovery.dcr_supported ? "success" : "info"}
                  >
                    Discovery passed. PKCE S256 supported.{" "}
                    {discovery.dcr_supported
                      ? "Dynamic registration available."
                      : "Dynamic registration unavailable; use a manual client."}
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
                    disabled={oauth.pending}
                  />
                  <FormControlLabel
                    value="manual"
                    control={<Radio />}
                    label="Manual client"
                    disabled={oauth.pending}
                  />
                </RadioGroup>
                {registration === "manual" && (
                  <>
                    <TextField
                      label="Client ID"
                      size="small"
                      value={clientId}
                      onChange={(event) => setClientId(event.target.value)}
                      disabled={oauth.pending}
                      autoComplete="off"
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
