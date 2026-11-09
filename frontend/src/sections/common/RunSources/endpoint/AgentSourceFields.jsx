// The secret-bearing fields of the A2A and MCP cards (W6 B4), mounted by
// the cards through one import each:
// - A2aAuthField: None · Bearer · Basic · API key · OAuth 2 client
//   credentials, each with its secret from the one workspace picker;
// - A2aTimeline: the task states a Test went through (submitted → working
//   → tasks/get polls → final), as the run's spans record them;
// - McpEnvField: the server's env variable and the secret it is set from.
import React from "react";
import PropTypes from "prop-types";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";
import SecretSelect from "./SecretSelect";
import { useSecretNamesState } from "./useSecretNamesState";
import {
  AUTH_KINDS,
  authFieldsOf,
  authIssues,
  authOf,
  authSummary,
} from "./a2aAuthModel";

const sel = { bgcolor: "background.paper", minWidth: 0 };
const FIELD_LABEL = {
  header: ["Header", "X-Api-Key"],
  username: ["User name", "support-bot"],
  token_url: ["Token URL", "https://auth.acme.test/oauth/token"],
  client_id: ["Client id", "fi-experiments"],
};

export function A2aAuthField({ auth, onChange }) {
  const names = useSecretNamesState();
  const a = authOf(auth);
  const set = (patch) => onChange({ ...a, ...patch });
  // Copy for the missing parts; a missing secret shows under the picker.
  const issues = authIssues(a, null);
  return (
    <Stack spacing={0.5} data-testid="a2a-auth">
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Auth"
          value={a.kind}
          onChange={(e) =>
            onChange({ kind: e.target.value, secret: a.secret ?? "" })
          }
          sx={{ ...sel, width: 230 }}
          inputProps={{ "data-testid": "a2a-auth-kind" }}
        >
          {AUTH_KINDS.map((k) => (
            <MenuItem key={k.id} value={k.id}>
              {k.label}
            </MenuItem>
          ))}
        </TextField>
        {authFieldsOf(a.kind).map((f) =>
          f === "secret" ? (
            <SecretSelect
              key={f}
              value={a.secret ?? ""}
              names={names ?? undefined}
              onChange={(v) => set({ secret: v })}
              testId="a2a-auth-secret"
            />
          ) : (
            <TextField
              key={f}
              size="small"
              label={FIELD_LABEL[f][0]}
              placeholder={FIELD_LABEL[f][1]}
              value={a[f] ?? ""}
              onChange={(e) => set({ [f]: e.target.value })}
              sx={{ ...sel, width: f === "token_url" ? 280 : 170 }}
              inputProps={{ "data-testid": `a2a-auth-${f}` }}
            />
          ),
        )}
      </Stack>
      <Typography
        typography="s3"
        color="text.secondary"
        data-testid="a2a-auth-summary"
      >
        {authSummary(a)}
      </Typography>
      {a.kind === "oauth2" ? (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="a2a-auth-oauth-note"
        >
          Before each task, Future AGI requests a token from the token URL
          (client credentials) and sends it as a Bearer token.
        </Typography>
      ) : null}
      {issues
        .filter((t) => !/^No workspace secret/.test(t))
        .map((t) => (
          <Typography key={t} typography="s3" color="warning.dark">
            {t}
          </Typography>
        ))}
    </Stack>
  );
}
A2aAuthField.propTypes = {
  auth: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

export function A2aTimeline({ timeline, headers }) {
  if (!timeline?.length) return null;
  const lines = Object.entries(headers ?? {});
  return (
    <Stack spacing={0.25} data-testid="a2a-test-timeline" sx={{ mt: 0.5 }}>
      {lines.map(([name, value]) => (
        <Typography
          key={name}
          typography="s3"
          color="text.secondary"
          sx={{ fontFamily: "monospace" }}
          data-testid="a2a-test-header"
        >
          {`${name}: ${value}`}
        </Typography>
      ))}
      {timeline.map((s, i) => (
        <Typography
          key={`${s.name}-${i}`}
          typography="s3"
          color={s.state === "failed" ? "error.main" : "text.secondary"}
          sx={{ fontFamily: "monospace" }}
        >
          {`${s.method ? `${s.method} · ` : ""}${s.state ?? s.name}${s.http_status ? ` · ${s.http_status}` : ""} · ${s.latency_ms} ms${s.chunks ? ` · ${s.chunks} streamed chunks` : ""}`}
        </Typography>
      ))}
    </Stack>
  );
}
A2aTimeline.propTypes = {
  timeline: PropTypes.arrayOf(PropTypes.object),
  headers: PropTypes.object,
};

// The field's rows `[{name, secret}]` as typed (the older single
// `env_secret` reads as one row; an empty server shows one blank row).
const mcpEnvRows = (server = {}) =>
  Array.isArray(server.env) && server.env.length
    ? server.env
    : server.env_secret
      ? [{ name: server.env_secret, secret: server.env_secret }]
      : [{ name: "", secret: "" }];

export function McpEnvField({ server, onChange }) {
  const names = useSecretNamesState();
  const env = mcpEnvRows(server);
  const e = env[0];
  const set = (patch) => onChange([{ ...e, ...patch }, ...env.slice(1)]);
  return (
    <Stack direction="row" gap={1} alignItems="center" data-testid="mcp-env">
      <TextField
        size="small"
        label="Env variable"
        placeholder="ACME_TOKEN"
        value={e.name ?? ""}
        onChange={(ev) => set({ name: ev.target.value })}
        sx={{ ...sel, width: 150, flexShrink: 0 }}
        inputProps={{ "data-testid": "mcp-env-name" }}
      />
      <Typography typography="s2" color="text.secondary">
        from
      </Typography>
      <SecretSelect
        value={e.secret ?? ""}
        names={names ?? undefined}
        onChange={(v) => set({ secret: v })}
        testId="mcp-env-secret"
        minWidth={170}
      />
    </Stack>
  );
}
McpEnvField.propTypes = {
  server: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
