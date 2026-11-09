import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { ConfirmDialog } from "src/components/custom-dialog";

import { useCreateToken, useMyTokens, useRevokeToken } from "../api/queries";
import { formatDate } from "../lib/format";
import { hubCommand } from "../lib/registry";

import CopyCommand from "./CopyCommand";
import { HubSection } from "./HubStates";

// Hub tokens (SPEC §9.3): one publisher each, publish scope, shown once.
// The CLI sends them as X-Omega-Hub-Token; they can never like.
export default function TokenSettings({ publishers }) {
  const tokens = useMyTokens();
  const create = useCreateToken();
  const revoke = useRevokeToken();
  const [name, setName] = useState("my laptop");
  const [publisher, setPublisher] = useState(publishers[0]?.handle || "");
  const [days, setDays] = useState("90");
  const [revoking, setRevoking] = useState(null);
  const [revoked, setRevoked] = useState(false);
  const rows = (tokens.data || []).filter((t) => !t.revokedAt);
  const confirmRevoke = async () => {
    if (!revoking || revoke.isPending) return;
    setRevoked(false);
    try {
      await revoke.mutateAsync(revoking.id);
      setRevoked(true);
    } catch {
      // The error stays under the list; a failed delete never removes a row.
    } finally {
      setRevoking(null);
    }
  };
  return (
    <HubSection title="Hub tokens" testId="hub-tokens">
      <Typography typography="s2" color="text.secondary">
        A token lets the Omega CLI publish for one publisher. Paste it into{" "}
        <Box component="code" sx={{ fontFamily: "monospace", fontSize: 13 }}>
          {hubCommand(["login", "--token-stdin"])}
        </Box>
        .
      </Typography>
      {create.data?.token && (
        <Alert severity="success" data-testid="hub-token-once">
          Copy it now; it is shown once.
          <Box sx={{ mt: 1 }}>
            <CopyCommand
              text={create.data.token}
              prompt=""
              label="Copy the token"
            />
          </Box>
        </Alert>
      )}
      {publishers.length ? (
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 120px auto" },
            alignItems: "start",
          }}
        >
          <TextField
            size="small"
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ maxLength: 60 }}
          />
          <TextField
            select
            size="small"
            label="Publisher"
            value={publisher}
            onChange={(e) => setPublisher(e.target.value)}
          >
            {publishers.map((p) => (
              <MenuItem
                key={p.handle}
                value={p.handle}
              >{`@${p.handle}`}</MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Expires in"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          >
            {["30", "90", "365"].map((d) => (
              <MenuItem key={d} value={d}>{`${d} days`}</MenuItem>
            ))}
          </TextField>
          <Button
            variant="contained"
            size="small"
            sx={{ height: 40 }}
            disabled={!name.trim() || !publisher || create.isPending}
            onClick={() =>
              create.mutate({
                name: name.trim(),
                publisher,
                days: Number(days),
              })
            }
          >
            Create token
          </Button>
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary">
          Create a publisher first.
        </Typography>
      )}
      {create.isError && <Alert severity="error">{create.error.message}</Alert>}
      {rows.length > 0 && (
        <Box
          component="ul"
          sx={{
            m: 0,
            p: 0,
            listStyle: "none",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {rows.map((token, index) => (
            <Box
              component="li"
              key={token.id}
              sx={{
                display: "flex",
                gap: 1.5,
                alignItems: "center",
                px: 1.5,
                py: 1,
                borderTop: index ? "1px solid" : 0,
                borderColor: "divider",
                flexWrap: "wrap",
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography typography="s2" fontWeight={600}>
                  {token.name}
                </Typography>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ fontFamily: "monospace" }}
                >
                  {`${token.prefix}… · @${token.publisher} · expires ${formatDate(token.expiresAt)}${token.lastUsedAt ? ` · last used ${formatDate(token.lastUsedAt)}` : " · never used"}`}
                </Typography>
              </Box>
              <Button
                size="small"
                color="error"
                disabled={revoke.isPending && revoke.variables === token.id}
                onClick={() => {
                  revoke.reset();
                  setRevoked(false);
                  setRevoking(token);
                }}
              >
                Revoke
              </Button>
            </Box>
          ))}
        </Box>
      )}
      {revoke.isError && <Alert severity="error">{revoke.error.message}</Alert>}
      {revoked && (
        <Alert severity="success" role="status">
          Token revoked
        </Alert>
      )}
      <ConfirmDialog
        open={Boolean(revoking)}
        onClose={() => !revoke.isPending && setRevoking(null)}
        closeDisabled={revoke.isPending}
        title={`Revoke ${revoking?.name || ""}?`}
        content="The CLI cannot use this token for new publishing requests."
        action={
          <Button
            variant="contained"
            color="error"
            disabled={revoke.isPending}
            onClick={confirmRevoke}
          >
            Revoke token
          </Button>
        }
      />
    </HubSection>
  );
}

TokenSettings.propTypes = { publishers: PropTypes.array.isRequired };
