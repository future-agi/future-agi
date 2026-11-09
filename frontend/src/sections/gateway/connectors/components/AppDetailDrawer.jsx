import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import ConfirmDialog from "src/sections/gateway/components/ConfirmDialog";
import { useOrganization } from "src/contexts/OrganizationContext";

import {
  DetailRow,
  DetailSection,
  GatewayDetailDrawer,
} from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import {
  APP_STATUS,
  appStatus,
  categoryLabel,
  currentProvider,
  isSignInServer,
  placementText,
  signInServed,
  vendorStatusText,
} from "../catalog-model";
import { connectTarget, openSignIn, serverSignIn } from "../connect-flow";
import { useRemoveConnector } from "../hooks/useConnectorCatalog";
import { useStartConnect } from "../hooks/useMyConnections";
import { ownAppOffer, useOwnOAuthApp } from "../hooks/useOwnOAuthApp";
import { registrationHold } from "../registration-copy";
import AccessControl from "./AccessControl";
import AppLogo from "./AppLogo";
import ConnectForm from "./ConnectForm";
import OwnOAuthApp from "./OwnOAuthApp";
import MCPOAuthApp from "./MCPOAuthApp";
import { AppStatusChip, TierBadge } from "./chips";
import SignInAccounts from "./SignInAccounts";

// A server is turned off only by `enabled: false` on it in the org's
// gateway configuration; no page has a switch for it. A server added from
// the catalog has no such flag, so disconnecting and connecting again
// turns the app back on.
function statusText(app, status, provider, canManage, signIn) {
  if (status === APP_STATUS.CONNECTED) return `${app.name} is connected.`;
  if (status === APP_STATUS.OFF) {
    const off = app.servers.find((server) => server.enabled === false);
    const where = `${app.name} is added but turned off in the organization's gateway configuration (mcp.servers.${off.server_id}.enabled is false), so agents cannot call it.`;
    if (!canManage) return where;
    if (!provider) {
      // Added with a provider the catalog no longer offers: after a
      // disconnect the app is listed only if another provider remains.
      return app.providers.length
        ? `${where} It was added with a provider the catalog no longer offers: disconnect it, then connect it with another provider.`
        : `${where} It was added with a provider the catalog no longer offers: disconnect it to remove it.`;
    }
    // Disconnect revokes the stored key, so a keyed app takes it again.
    const again =
      provider.credential?.mode === "shared"
        ? "connect it again with its key"
        : "connect it again";
    return `${where} To turn it on from here, disconnect it and ${again}.`;
  }
  if (status === APP_STATUS.NOT_CONNECTED) {
    const server = app.servers[0];
    const perUser = server.credential_mode === "per_user";
    if (signIn) {
      return perUser
        ? `${app.name} is added. Sign in to your own ${app.name} account so agents can call it for you.`
        : `${app.name} is added. Sign in to the organization's ${app.name} account so agents can call it.`;
    }
    if (!perUser && !canManage && signInServed(provider)) {
      return `${app.name} is added, but an Owner or Admin has not signed in to its account yet, so agents cannot call it.`;
    }
    const fact = `${app.name} is added, but this deployment cannot connect its account, so agents cannot call it.`;
    return canManage ? `${fact} Disconnect it to remove it.` : fact;
  }
  if (status === APP_STATUS.NEEDS_KEY) {
    return provider
      ? `${app.name} is added, but its key is not stored, so the gateway cannot call it yet.`
      : `${app.name} was added with a provider the catalog no longer offers. Disconnect it to add it again.`;
  }
  return null;
}

// The app's facts as the gateway drawer's label and value rows.
function Details({ provider, app }) {
  const vendorStatus = vendorStatusText(provider);
  const docs = app.docs_url?.startsWith("https://") ? app.docs_url : null;
  return (
    <DetailSection title="Details">
      <Box>
        {provider && (
          <DetailRow label="Server" mono>
            {provider.url_template}
          </DetailRow>
        )}
        {provider && (
          <DetailRow label="Catalog entry" mono>
            {provider.catalog_ref}
          </DetailRow>
        )}
        {vendorStatus && (
          <DetailRow label="Vendor status">{vendorStatus}</DetailRow>
        )}
        {docs && (
          <DetailRow label="Docs">
            <Link href={docs} target="_blank" rel="noopener noreferrer">
              {docs}
            </Link>
          </DetailRow>
        )}
      </Box>
    </DetailSection>
  );
}

Details.propTypes = {
  provider: PropTypes.object,
  app: PropTypes.object.isRequired,
};

// The drawer's title: the app's mark and name (the mark is decorative).
function Title({ app }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 1.25 }}
    >
      <AppLogo app={app} size={28} />
      <span>{app.name}</span>
    </Box>
  );
}

Title.propTypes = { app: PropTypes.object.isRequired };

// The provider picked in the connect form, while the app has no server yet
// and the pick was made for this app; null means the app's own provider.
function pickedProvider(app, picked) {
  if (!app || app.servers?.[0] || picked?.appId !== app.id) return null;
  return (
    (app.providers || []).find((card) => card.id === picked.providerId) || null
  );
}

// One app, on the gateway detail drawer: what it is, whether it is
// connected, the connect form, access control (read) and its facts, with
// Disconnect in the drawer's header once it is added. Connecting and
// disconnecting change the org's gateway config: Owner or Admin only. For
// anyone else the control shows disabled and says why (GatewayAdminOnly),
// and no key field is drawn.
export default function AppDetailDrawer({
  app,
  gatewayId,
  canManage,
  upstreamOAuth = false,
  onClose,
  onCategory,
}) {
  const [confirming, setConfirming] = useState(false);
  const { currentOrganizationId } = useOrganization();
  const removeConnector = useRemoveConnector();
  const { start, pending: starting } = useStartConnect();
  const [startError, setStartError] = useState(null);
  // The drawer and the connect form share the picked provider: the drawer
  // describes the provider the add would use, and a provider configured as
  // an MCP OAuth app shows that configuration, never an add the API refuses
  // (oauth_configuration_required). Another app, or a closed drawer, starts
  // again from the app's own provider.
  const [picked, setPicked] = useState(null);
  if (picked && picked.appId !== app?.id) setPicked(null);

  const provider = app
    ? pickedProvider(app, picked) || currentProvider(app)
    : null;
  const configureOAuth = provider?.connect?.configure_oauth === true;
  const ownApp = useOwnOAuthApp(upstreamOAuth ? provider : null, canManage);
  // A sign-in only the org's own OAuth app can serve waits for that app.
  const offer = ownAppOffer(provider);
  const ownAppMissing = provider?.connect?.state
    ? provider.connect.state !== "ready"
    : Boolean(ownApp.offer?.required) && !ownApp.loading && !ownApp.current;
  const registrationText =
    ownAppMissing && offer
      ? registrationHold(app.name, offer.clientGroup)
      : null;
  const holdText =
    !canManage && registrationText
      ? registrationText.replace(
          "Add it below to connect.",
          "Ask an Owner or Admin to register it.",
        )
      : registrationText;
  const server = app?.servers?.[0] || null;
  const status = app ? appStatus(app) : null;
  // Where this user signs in to the added app's account, when the API names
  // one this user may follow (connect-flow.js serverSignIn).
  const signIn =
    status === APP_STATUS.NOT_CONNECTED && !ownAppMissing && !ownApp.loading
      ? connectTarget(server?.start_url) ||
        serverSignIn(server, provider, canManage)
      : null;
  const text = app
    ? statusText(app, status, provider, canManage, signIn)
    : null;
  const addable =
    Boolean(provider) &&
    (status === APP_STATUS.AVAILABLE || status === APP_STATUS.NEEDS_KEY);
  const connectable = addable && !configureOAuth;
  // With an OAuth app provider picked, an Owner or Admin keeps the provider
  // choice, so a key provider can be picked again.
  const choosing = !server && (app?.providers?.length || 0) > 1;
  const showForm = canManage && addable && (!configureOAuth || choosing);
  const facets = app
    ? [app.ee_category, ...(app.categories || [])].filter(
        (id, index, all) => id && all.indexOf(id) === index,
      )
    : [];

  const disconnect = async () => {
    try {
      const result = await removeConnector.mutateAsync({ appId: app.id });
      setConfirming(false);
      enqueueSnackbar(`${app.name} is disconnected.`, { variant: "success" });
      if (result?.gateway_synced === false) {
        enqueueSnackbar(
          result.gateway_warning ||
            "The gateway has not picked up the change yet.",
          { variant: "warning" },
        );
      }
    } catch (err) {
      setConfirming(false);
      enqueueSnackbar(
        err?.message || `${app.name} could not be disconnected.`,
        { variant: "error" },
      );
    }
  };

  return (
    <GatewayDetailDrawer
      open={Boolean(app)}
      onClose={onClose}
      width={520}
      title={app ? <Title app={app} /> : ""}
      subtitle={
        app?.vendor?.name && app.vendor.name !== app.name
          ? app.vendor.name
          : undefined
      }
      actions={
        app && server ? (
          <GatewayAdminOnly allowed={canManage}>
            <Button
              size="small"
              variant="outlined"
              color="error"
              onClick={() => setConfirming(true)}
              disabled={!canManage}
            >
              Disconnect
            </Button>
          </GatewayAdminOnly>
        ) : null
      }
    >
      {app && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
            <Box
              sx={{
                display: "flex",
                flexWrap: "wrap",
                gap: 0.75,
                alignItems: "center",
              }}
            >
              <TierBadge provider={provider} />
              <AppStatusChip status={status} />
              {facets.map((id) => (
                <Chip
                  key={id}
                  size="small"
                  variant="outlined"
                  label={categoryLabel(id)}
                  clickable
                  component="button"
                  aria-label={`Show ${categoryLabel(id)} apps`}
                  onClick={() => onCategory(id)}
                  sx={{ height: 22, typography: "s3" }}
                />
              ))}
            </Box>
            <Typography typography="s1" color="text.primary">
              {app.description}
            </Typography>
          </Box>

          <Box
            component="section"
            aria-label="Connection"
            sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}
          >
            {text && !configureOAuth && (
              <Alert
                severity={status === APP_STATUS.CONNECTED ? "success" : "info"}
              >
                {text}
              </Alert>
            )}
            {status === APP_STATUS.CONNECTED && provider && (
              <Typography typography="s2" color="text.secondary">
                {placementText(provider.credential)}
              </Typography>
            )}
            {signIn && (
              <Box>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={Boolean(starting)}
                  onClick={async () => {
                    setStartError(null);
                    if (!server.start_url) {
                      openSignIn(signIn);
                      return;
                    }
                    try {
                      await start(server);
                    } catch (err) {
                      setStartError(err.message || "Sign-in could not start.");
                    }
                  }}
                >
                  {`Sign in to ${app.name}`}
                </Button>
              </Box>
            )}
            {startError && <Alert severity="error">{startError}</Alert>}
            {holdText && (
              <Typography typography="s2" color="text.secondary">
                {holdText}
              </Typography>
            )}
            {showForm && (
              <ConnectForm
                key={app.id}
                app={app}
                provider={provider}
                providers={app.providers}
                onProvider={(providerId) =>
                  setPicked({ appId: app.id, providerId })
                }
                server={status === APP_STATUS.NEEDS_KEY ? server : null}
                held={ownAppMissing || ownApp.loading}
              />
            )}
            {!canManage && !signIn && (
              <Typography typography="s2" color="text.secondary">
                Only an organization Owner or Admin can connect apps.
              </Typography>
            )}
            {!canManage && connectable && (
              <Box>
                <GatewayAdminOnly allowed={false}>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    disabled
                  >
                    {status === APP_STATUS.NEEDS_KEY ? "Save key" : "Connect"}
                  </Button>
                </GatewayAdminOnly>
              </Box>
            )}
          </Box>

          {configureOAuth && (
            <MCPOAuthApp
              key={`${currentOrganizationId}:${app.id}:${provider.id}:${Boolean(server)}`}
              app={app}
              provider={provider}
              canManage={canManage}
            />
          )}
          <SignInAccounts app={app} server={server} canManage={canManage} />
          {ownApp.offer && <OwnOAuthApp app={app} ownApp={ownApp} />}
          <Divider />
          <AccessControl
            gatewayId={gatewayId}
            canManage={canManage}
            appName={app.name}
            provider={provider}
            server={server}
          />
          <Divider />
          <Details provider={provider} app={app} />
        </Box>
      )}

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={disconnect}
        title={app ? `Disconnect ${app.name}?` : ""}
        message={
          server && isSignInServer(server, provider)
            ? "This removes the app's server and virtual server from the gateway and revokes the gateway's access to every account signed in to it. Agents lose its tools at once."
            : "This removes the app's server and virtual server from the gateway and revokes its stored key. Agents lose its tools at once."
        }
        confirmLabel="Disconnect"
        isLoading={removeConnector.isPending}
      />
    </GatewayDetailDrawer>
  );
}

AppDetailDrawer.propTypes = {
  app: PropTypes.object,
  gatewayId: PropTypes.string,
  canManage: PropTypes.bool.isRequired,
  upstreamOAuth: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onCategory: PropTypes.func.isRequired,
};
