import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Skeleton from "@mui/material/Skeleton";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { ConfirmDialog } from "src/components/custom-dialog";

import { useCreateToken, useMyTokens, useRevokeToken } from "../api/queries";
import { formatDate, timeAgo } from "../lib/format";
import { hubCommand } from "../lib/registry";

import CopyCommand, { copyText } from "./CopyCommand";
import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";
import { problemText } from "./PublisherSettings";

// Hub tokens (SPEC §9.3): one publisher each, publish scope, shown once.
// The CLI sends them as X-Omega-Hub-Token; they can never like. The list is a
// table on wide screens (a header row and one row per token) and stacked rows
// on narrow ones; it stays a list of rows so each row keeps its own Revoke.

const REVOKE_CONSEQUENCE =
  "The CLI cannot use this token for new publishing requests.";
const EXPIRY_DAYS = ["30", "90", "365"];
const NO_ANSWER = "The Hub registry did not answer; try again in a moment.";
const DAY_MS = 24 * 60 * 60 * 1000;
const COLUMNS =
  "minmax(0, 1.3fr) minmax(0, 1.1fr) minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.2fr) minmax(0, 1.2fr) 88px";
const HEADERS = [
  "Name",
  "Token prefix",
  "Scope",
  "Created",
  "Last used",
  "Expires",
];
const VISUALLY_HIDDEN = {
  position: "absolute",
  width: "1px",
  height: "1px",
  margin: "-1px",
  padding: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

// The whole moment in one stated time zone: "8 Nov 2026, 14:03 UTC".
function utcStamp(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return `${formatDate(iso)}, ${date.toISOString().slice(11, 16)} UTC`;
}

function expiresIn(iso, now = Date.now()) {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return null;
  const days = Math.ceil((time - now) / DAY_MS);
  if (days <= 0) return "Expired";
  return days === 1 ? "in 1 day" : `in ${days} days`;
}

function Code({ children }) {
  return (
    <Box
      component="code"
      sx={{ fontFamily: "monospace", fontSize: 12, color: "text.primary" }}
    >
      {children}
    </Box>
  );
}

Code.propTypes = { children: PropTypes.node };

// A cell's column name: shown above the value on narrow screens, read by
// screen readers (and hidden on screen) where the header row shows it.
function Cell({ label, children }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        component="span"
        typography="s2"
        color="text.secondary"
        sx={(theme) => ({
          display: "block",
          mb: 0.25,
          [theme.breakpoints.up("lg")]: VISUALLY_HIDDEN,
        })}
      >
        {label}
      </Typography>
      {children}
    </Box>
  );
}

Cell.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

// A relative time over the exact UTC moment.
function Moment({ iso, relative, tone }) {
  const stamp = utcStamp(iso);
  if (!stamp) {
    return (
      <Typography typography="s1" color="text.secondary">
        —
      </Typography>
    );
  }
  return (
    <Box component="time" dateTime={iso} sx={{ display: "block" }}>
      <Typography
        component="span"
        typography="s1"
        color={tone || "text.primary"}
        sx={{ display: "block" }}
      >
        {relative}
      </Typography>
      <Typography
        component="span"
        typography="s2"
        color="text.secondary"
        sx={{ display: "block" }}
      >
        {stamp}
      </Typography>
    </Box>
  );
}

Moment.propTypes = {
  iso: PropTypes.string,
  relative: PropTypes.string,
  tone: PropTypes.string,
};

function PrefixCopy({ token }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(token.prefix)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}>
      <Box
        component="code"
        sx={{
          fontFamily: "monospace",
          fontSize: 12,
          color: "text.primary",
          overflowWrap: "anywhere",
        }}
      >
        {`${token.prefix}…`}
      </Box>
      <Tooltip title={copied ? "Copied" : "Copy the prefix"}>
        <IconButton
          size="small"
          onClick={onCopy}
          aria-label={`Copy the prefix of ${token.name}`}
        >
          <HubIcon name={copied ? "check" : "copy"} size={14} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

PrefixCopy.propTypes = { token: PropTypes.object.isRequired };

// A token list that did not load, or did not refresh, says so with Try again.
function LoadFailed({ children, onRetry }) {
  return (
    <Alert
      severity="error"
      action={
        <Button size="small" color="inherit" onClick={onRetry}>
          Try again
        </Button>
      }
    >
      {children}
    </Alert>
  );
}

LoadFailed.propTypes = {
  children: PropTypes.node,
  onRetry: PropTypes.func.isRequired,
};

// One row per token. Revoke keeps its short name and reads the token's name
// as its description, so a screen reader names the token it revokes.
function TokenList({ rows, ids, pendingId, onRevoke }) {
  const now = Date.now();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          display: { xs: "none", lg: "grid" },
          gridTemplateColumns: COLUMNS,
          columnGap: 2,
          px: 2,
          py: 1,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
          typography: "s2",
          fontWeight: 500,
          color: "text.secondary",
        }}
      >
        {HEADERS.map((header) => (
          <span key={header}>{header}</span>
        ))}
        <span />
      </Box>
      <Box component="ul" sx={{ m: 0, p: 0, listStyle: "none" }}>
        {rows.map((token, index) => {
          const expires = expiresIn(token.expiresAt, now);
          const nameId = `${ids}-token-${token.id}`;
          return (
            <Box
              component="li"
              key={token.id}
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "minmax(0, 1fr) auto",
                  lg: COLUMNS,
                },
                columnGap: 2,
                rowGap: 1.25,
                alignItems: { xs: "start", lg: "center" },
                px: 2,
                py: 1.5,
                borderTop: index ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              <Typography
                id={nameId}
                typography="s1"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{ minWidth: 0, overflowWrap: "anywhere" }}
              >
                {token.name}
              </Typography>
              <Box
                sx={{
                  gridColumn: { xs: "1 / -1", lg: "auto" },
                  gridRow: { xs: 2, lg: "auto" },
                  display: { xs: "grid", lg: "contents" },
                  gridTemplateColumns: "repeat(auto-fill, minmax(128px, 1fr))",
                  gap: 1.5,
                }}
              >
                <Cell label="Token prefix">
                  <PrefixCopy token={token} />
                </Cell>
                <Cell label="Scope">
                  <Typography
                    typography="s1"
                    color="text.primary"
                    sx={{ fontFamily: "monospace" }}
                  >
                    {(token.scopes || []).join(", ") || "publish"}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {`as @${token.publisher}`}
                  </Typography>
                </Cell>
                <Cell label="Created">
                  <Moment
                    iso={token.createdAt}
                    relative={timeAgo(token.createdAt)}
                  />
                </Cell>
                <Cell label="Last used">
                  {token.lastUsedAt ? (
                    <Moment
                      iso={token.lastUsedAt}
                      relative={timeAgo(token.lastUsedAt)}
                    />
                  ) : (
                    <Typography typography="s1" color="text.secondary">
                      Never used
                    </Typography>
                  )}
                </Cell>
                <Cell label="Expires">
                  <Moment
                    iso={token.expiresAt}
                    relative={expires}
                    tone={expires === "Expired" ? "error.main" : null}
                  />
                </Cell>
              </Box>
              <Box
                sx={{
                  gridColumn: { xs: 2, lg: "auto" },
                  gridRow: { xs: 1, lg: "auto" },
                  justifySelf: "end",
                }}
              >
                <Button
                  size="small"
                  color="error"
                  aria-describedby={nameId}
                  disabled={pendingId === token.id}
                  onClick={() => onRevoke(token)}
                >
                  Revoke
                </Button>
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

TokenList.propTypes = {
  rows: PropTypes.array.isRequired,
  ids: PropTypes.string.isRequired,
  pendingId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onRevoke: PropTypes.func.isRequired,
};

export default function TokenSettings({ publishers }) {
  const tokens = useMyTokens();
  const create = useCreateToken();
  const revoke = useRevokeToken();
  const ids = useId();
  const [name, setName] = useState("my laptop");
  const [picked, setPicked] = useState(publishers[0]?.handle || "");
  const [days, setDays] = useState("90");
  const [revoking, setRevoking] = useState(null);
  const [revoked, setRevoked] = useState(false);
  // The chosen publisher stays one of the list as the list changes: one made
  // on this page is chosen at once, and a suspended one drops out.
  const publisher = publishers.some((p) => p.handle === picked)
    ? picked
    : publishers[0]?.handle || "";
  const rows = (tokens.data || []).filter((t) => !t.revokedAt);
  const loadProblem = problemText(tokens.error, NO_ANSWER);
  const canCreate = Boolean(name.trim() && publisher) && !create.isPending;
  const submit = (event) => {
    event.preventDefault();
    if (!canCreate) return;
    create.mutate({ name: name.trim(), publisher, days: Number(days) });
  };
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
    <HubPanel
      title="Hub tokens"
      testId="hub-tokens"
      description="A token lets the Omega CLI publish as one publisher. Sign in once on each machine: the command asks for the token, and what you paste stays hidden."
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
        <CopyCommand text={hubCommand(["login"])} />
        <Typography typography="s2" color="text.secondary">
          In CI, add <Code>--token-stdin</Code> to this command and pipe the
          token in.
        </Typography>
      </Box>
      <Box
        component="form"
        noValidate
        onSubmit={submit}
        aria-labelledby={`${ids}-new`}
        sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}
      >
        <Typography
          id={`${ids}-new`}
          component="h3"
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          Create a token
        </Typography>
        {publishers.length ? (
          <Box
            sx={{
              display: "grid",
              gap: 1.5,
              alignItems: "start",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                sm: "repeat(2, minmax(0, 1fr))",
                lg: "minmax(0, 1.4fr) minmax(0, 1.2fr) minmax(120px, 0.6fr) auto",
              },
            }}
          >
            <TextField
              id={`${ids}-name`}
              size="small"
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              helperText="Name it after the machine or CI job that uses it."
              inputProps={{ maxLength: 60 }}
            />
            <TextField
              id={`${ids}-publisher`}
              select
              size="small"
              label="Publisher"
              value={publisher}
              onChange={(e) => setPicked(e.target.value)}
            >
              {publishers.map((p) => (
                <MenuItem
                  key={p.handle}
                  value={p.handle}
                >{`@${p.handle}`}</MenuItem>
              ))}
            </TextField>
            <TextField
              id={`${ids}-days`}
              select
              size="small"
              label="Expires in"
              value={days}
              onChange={(e) => setDays(e.target.value)}
            >
              {EXPIRY_DAYS.map((d) => (
                <MenuItem key={d} value={d}>{`${d} days`}</MenuItem>
              ))}
            </TextField>
            <Button
              type="submit"
              variant="contained"
              sx={{ mt: { sm: 3 }, justifySelf: "start" }}
              disabled={!canCreate}
            >
              Create token
            </Button>
          </Box>
        ) : (
          <Typography typography="s1" color="text.secondary">
            A token publishes as one publisher. Create your publisher above
            first.
          </Typography>
        )}
      </Box>
      {create.data?.token && (
        <Alert
          severity="success"
          data-testid="hub-token-once"
          sx={{ "& .MuiAlert-message": { minWidth: 0, flex: 1 } }}
        >
          {`Token ${create.data.name || ""} created. Copy it now; it is shown once. Then sign in with the command above and paste it when asked.`}
          <Box sx={{ mt: 1 }}>
            <CopyCommand
              text={create.data.token}
              prompt=""
              label="Copy the token"
            />
          </Box>
        </Alert>
      )}
      {create.isError && (
        <Alert severity="error">
          {problemText(create.error, `The token was not created. ${NO_ANSWER}`)}
        </Alert>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
        <Typography
          component="h3"
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          {rows.length ? `Your tokens (${rows.length})` : "Your tokens"}
        </Typography>
        {tokens.isLoading ? (
          <Box sx={{ display: "grid", gap: 1 }} aria-busy="true">
            <Skeleton variant="rounded" height={52} />
            <Skeleton variant="rounded" height={52} />
          </Box>
        ) : rows.length ? (
          <>
            {tokens.isError && (
              <LoadFailed onRetry={() => tokens.refetch()}>
                {`The token list did not refresh and may be out of date. ${loadProblem}`}
              </LoadFailed>
            )}
            <TokenList
              rows={rows}
              ids={ids}
              pendingId={revoke.isPending ? revoke.variables : null}
              onRevoke={(token) => {
                revoke.reset();
                setRevoked(false);
                setRevoking(token);
              }}
            />
          </>
        ) : tokens.isError ? (
          <LoadFailed onRetry={() => tokens.refetch()}>
            {`Your tokens did not load. ${loadProblem}`}
          </LoadFailed>
        ) : (
          <Typography typography="s1" color="text.secondary">
            You have no Hub tokens. Create one for each machine or CI job that
            publishes.
          </Typography>
        )}
      </Box>
      {revoke.isError && (
        <Alert severity="error">
          {problemText(revoke.error, `The token was not revoked. ${NO_ANSWER}`)}
        </Alert>
      )}
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
        content={
          <>
            {REVOKE_CONSEQUENCE}
            {revoking
              ? ` It is ${revoking.prefix}…, publishing as @${revoking.publisher}. A revoked token cannot be used again; create a new one when you need it.`
              : ""}
          </>
        }
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
    </HubPanel>
  );
}

TokenSettings.propTypes = { publishers: PropTypes.array.isRequired };
