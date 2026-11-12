import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import ConfirmDialog from "src/sections/gateway/components/ConfirmDialog";

import { GatewayStatusChip } from "../../components/frame";
import { connectTarget } from "../connect-flow";
import { useDisconnectAccount } from "../hooks/useConnectorCatalog";

// An account's state as the vault keeps it (M14), on the gateway status chip.
const STATES = {
  active: { tone: "healthy", label: "Connected" },
  needs_reauth: { tone: "warning", label: "Needs a new sign-in" },
  expired: { tone: "warning", label: "Expired" },
  revoked: { tone: "neutral", label: "Disconnected" },
};

function whose(account) {
  if (account.mine === true) return "Your account";
  if (account.scope === "shared") return "Shared by the organization";
  return "A member's account";
}

// Where this user disconnects an account, or null: the API's disconnect_url
// on its own sign-in path, for the user's own account, or for an Owner or
// Admin any other account the API lets them disconnect.
function disconnectTarget(account, canManage) {
  const target = connectTarget(account.disconnect_url);
  if (!target) return null;
  return account.mine === true || canManage ? target : null;
}

function wording(app, account) {
  if (account.mine === true) {
    return {
      title: `Disconnect your ${app.name} account?`,
      done: `Your ${app.name} account is disconnected.`,
    };
  }
  if (account.scope === "shared") {
    return {
      title: `Disconnect the organization's ${app.name} account?`,
      done: `The organization's ${app.name} account is disconnected.`,
    };
  }
  return {
    title: `Disconnect ${account.label}?`,
    done: `${account.label} is disconnected.`,
  };
}

// The accounts on an added sign-in app this user may see (the server's
// `accounts`, connect-flow.js): each with whose it is and its state, and a
// Disconnect where the API names one this user may follow. Disconnecting
// revokes the gateway's access to that account; agents lose it at once.
export default function SignInAccounts({ app, server, canManage }) {
  const headingId = useId();
  const [confirming, setConfirming] = useState(null);
  const disconnect = useDisconnectAccount();
  const accounts = Array.isArray(server?.accounts) ? server.accounts : [];
  if (!accounts.length) return null;

  const confirm = async () => {
    const { account, target } = confirming;
    const { done } = wording(app, account);
    try {
      await disconnect.mutateAsync({ url: target });
      setConfirming(null);
      enqueueSnackbar(done, { variant: "success" });
    } catch (err) {
      setConfirming(null);
      enqueueSnackbar(
        err?.message || `${account.label} could not be disconnected.`,
        {
          variant: "error",
        },
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
        Accounts
      </Typography>
      <Box
        component="ul"
        sx={{
          listStyle: "none",
          m: 0,
          p: 0,
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {accounts.map((account, index) => {
          const state = STATES[account.status];
          const target = disconnectTarget(account, canManage);
          return (
            <Box
              component="li"
              key={account.id || index}
              sx={{
                display: "flex",
                gap: 1,
                alignItems: "center",
                justifyContent: "space-between",
                px: 1.5,
                py: 1,
                borderTop: index ? 1 : 0,
                borderColor: "divider",
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  typography="s2_1"
                  color="text.primary"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {account.label}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {whose(account)}
                </Typography>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "center",
                  flexShrink: 0,
                }}
              >
                {state && (
                  <GatewayStatusChip
                    tone={state.tone}
                    label={state.label}
                    showIcon={false}
                  />
                )}
                {target && (
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    aria-label={`Disconnect ${account.label}`}
                    onClick={() => setConfirming({ account, target })}
                  >
                    Disconnect
                  </Button>
                )}
              </Box>
            </Box>
          );
        })}
      </Box>

      <ConfirmDialog
        open={Boolean(confirming)}
        onClose={() => setConfirming(null)}
        onConfirm={confirm}
        title={confirming ? wording(app, confirming.account).title : ""}
        message="Agents stop using this account once the gateway confirms; you can also revoke access in your vendor account settings."
        confirmLabel="Disconnect"
        isLoading={disconnect.isPending}
      />
    </Box>
  );
}

SignInAccounts.propTypes = {
  app: PropTypes.shape({ name: PropTypes.string.isRequired }).isRequired,
  server: PropTypes.shape({
    accounts: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        scope: PropTypes.string,
        label: PropTypes.string,
        mine: PropTypes.bool,
        status: PropTypes.string,
        disconnect_url: PropTypes.string,
      }),
    ),
  }),
  canManage: PropTypes.bool.isRequired,
};
