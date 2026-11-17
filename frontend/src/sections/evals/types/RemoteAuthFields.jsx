// W7-4 remote scorers: how Future AGI signs in to the scorer (the editor's
// auth fields). HMAC: the platform makes the secret when you save and shows
// it once. Bearer: typed once, shown as its last 4. OAuth client credentials:
// token URL, client id, client secret (typed once) and scope.
import React from "react";
import PropTypes from "prop-types";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";
import {
  AUTH_KINDS,
  maskedToken,
} from "src/sections/common/EvalTypes/remoteModel";

export default function RemoteAuthFields({ form, set, shown, detail }) {
  const tokenLast4 = detail?.remote?.auth?.token_last4;
  return (
    <>
      <Stack direction="row" spacing={1.5}>
        <TextField
          select
          size="small"
          label="Sign in with"
          value={form.authKind}
          onChange={set("authKind")}
          sx={{ minWidth: 260 }}
          inputProps={{ "data-testid": "remote-auth" }}
        >
          {AUTH_KINDS.map((k) => (
            <MenuItem key={k.value} value={k.value}>
              {k.label}
            </MenuItem>
          ))}
        </TextField>
        {form.authKind === "hmac" && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ alignSelf: "center" }}
          >
            {detail?.signing?.secret_set
              ? "Signed with the secret you copied when you saved it."
              : "Future AGI makes the signing secret when you save. You see it once."}
          </Typography>
        )}
        {form.authKind === "bearer" && (
          <TextField
            size="small"
            type="password"
            label="Bearer token"
            value={form.token}
            onChange={set("token")}
            placeholder={tokenLast4 ? maskedToken(tokenLast4) : ""}
            error={Boolean(shown.auth)}
            helperText={
              shown.auth ??
              (tokenLast4
                ? `Stored: ${maskedToken(tokenLast4)}. Type a new one to replace it.`
                : "Typed once; shown as its last 4 after you save.")
            }
            sx={{ flex: 1 }}
            inputProps={{ "data-testid": "remote-bearer" }}
          />
        )}
      </Stack>
      {form.authKind === "oauth" && (
        <Stack direction="row" spacing={1.5}>
          <TextField
            size="small"
            label="Token URL"
            value={form.tokenUrl}
            onChange={set("tokenUrl")}
            error={Boolean(shown.token_url)}
            helperText={shown.token_url}
            sx={{ flex: 2 }}
            inputProps={{ "data-testid": "remote-token-url" }}
          />
          <TextField
            size="small"
            label="Client id"
            value={form.clientId}
            onChange={set("clientId")}
            error={Boolean(shown.client_id)}
            helperText={shown.client_id}
            sx={{ flex: 1 }}
            inputProps={{ "data-testid": "remote-client-id" }}
          />
          <TextField
            size="small"
            type="password"
            label="Client secret"
            value={form.clientSecret}
            onChange={set("clientSecret")}
            error={Boolean(shown.auth)}
            helperText={
              shown.auth ??
              (detail?.remote?.auth?.client_secret_set
                ? "Stored. Type a new one to replace it."
                : "")
            }
            sx={{ flex: 1 }}
            inputProps={{ "data-testid": "remote-client-secret" }}
          />
          <TextField
            size="small"
            label="Scope"
            value={form.scope}
            onChange={set("scope")}
            sx={{ flex: 1 }}
          />
        </Stack>
      )}
    </>
  );
}

RemoteAuthFields.propTypes = {
  form: PropTypes.object.isRequired,
  set: PropTypes.func.isRequired,
  shown: PropTypes.object.isRequired,
  detail: PropTypes.object,
};
