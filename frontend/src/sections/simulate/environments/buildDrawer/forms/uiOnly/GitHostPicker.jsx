import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  BTN_SX,
  INPUT_SX,
  OUTLINED_BTN_SX,
  PANEL_SX,
  ROW_BOX_SX,
  T12,
  T13,
} from "../../drawerStyles";
import { HintLine, MONO } from "../formBits";
import SampleTag from "../shared/SampleTag";
import SecretField from "../shared/SecretField";
import TextLink from "../shared/TextLink";
import { GIT_HOST_META } from "./sampleData";

const CONNECTING_MS = 700;

const selectSx = {
  ...INPUT_SX,
  "& .MuiSelect-select": {
    py: 0.75,
    px: 1,
    minHeight: "unset",
    ...T12,
  },
};

/** A select of repositories, or of anything else picked by name. */
export function NameSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
  renderMeta,
}) {
  return (
    <Select
      size="small"
      fullWidth
      displayEmpty
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      inputProps={{ "aria-label": label }}
      renderValue={(v) =>
        v ? (
          <Box component="span" sx={{ fontFamily: MONO }}>
            {v}
          </Box>
        ) : (
          <Box component="span" sx={{ color: "text.disabled" }}>
            {placeholder}
          </Box>
        )
      }
      sx={selectSx}
    >
      {options.map((o) => (
        <MenuItem key={o.name} value={o.name} sx={{ ...T12, gap: 1 }}>
          <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
            {o.name}
          </Box>
          {renderMeta && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {renderMeta(o)}
            </Box>
          )}
        </MenuItem>
      ))}
    </Select>
  );
}
NameSelect.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  options: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string }))
    .isRequired,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  renderMeta: PropTypes.func,
};

/**
 * The repository select once a host is connected: which groups or
 * workspaces it reads, and a link to connect another. The repositories are
 * samples.
 */
export function RepoPicker({
  host,
  accounts,
  repos,
  repo,
  onPick,
  onAddAccount,
}) {
  const meta = GIT_HOST_META[host];
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.5 }}
      >
        <Typography sx={{ ...T12, fontWeight: 600 }}>Repository</Typography>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {`· from ${meta.label}${accounts.length ? ` (${accounts.join(", ")})` : ""}${repo ? " · read only" : ""}`}
        </Typography>
        <SampleTag />
        <Box flex={1} />
        {onAddAccount && (
          <TextLink onClick={onAddAccount}>
            {`Connect another ${meta.account.toLowerCase()}`}
          </TextLink>
        )}
      </Stack>
      <NameSelect
        label="Repository"
        value={repo}
        options={repos}
        onChange={onPick}
        placeholder="Choose a repository"
        renderMeta={(r) => r.language}
      />
    </Box>
  );
}
RepoPicker.propTypes = {
  host: PropTypes.string.isRequired,
  accounts: PropTypes.arrayOf(PropTypes.string).isRequired,
  repos: PropTypes.array.isRequired,
  repo: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onAddAccount: PropTypes.func,
};

/**
 * Connecting GitLab or Bitbucket, once for the workspace: the group or
 * workspace and its access token, read scopes only. Write access is asked
 * for at the first draft merge or pull request. UI only: connecting is
 * simulated, and `onConnected(account)` lists that account's sample
 * repositories.
 */
export function GitHostConnect({ host, except = [], onConnected, onCancel }) {
  const meta = GIT_HOST_META[host];
  const [token, setToken] = useState("");
  const [account, setAccount] = useState(
    () => meta.accounts.find((a) => !except.includes(a)) || meta.accounts[0],
  );
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    if (!connecting) return undefined;
    const t = setTimeout(() => {
      setConnecting(false);
      onConnected(account);
    }, CONNECTING_MS);
    return () => clearTimeout(t);
  }, [connecting]); // eslint-disable-line react-hooks/exhaustive-deps

  const noun = meta.account.toLowerCase();
  return (
    <Box sx={PANEL_SX}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 1.75,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon={meta.icon}
          width={20}
          sx={{ color: "text.primary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ ...T13, fontWeight: 600 }}>
            {`Connect ${meta.label} to pick the agent`}
          </Typography>
          <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
            <HintLine
              text="Once for the workspace · read only"
              hint={`We scan the ${noun}'s repositories and find the agents in them. Write access is asked for at your first draft ${meta.review}.`}
            />
          </Typography>
        </Box>
      </Stack>
      <Stack spacing={1.5} sx={{ px: 1.75, py: 1.5 }}>
        <Box>
          <Typography sx={{ ...T12, fontWeight: 600, mb: 0.5 }}>
            {meta.account}
          </Typography>
          <NameSelect
            label={meta.account}
            value={account}
            options={meta.accounts.map((name) => ({ name }))}
            onChange={setAccount}
            renderMeta={(o) => (except.includes(o.name) ? "Connected now" : "")}
          />
        </Box>
        <SecretField
          label={meta.token}
          required
          value={token}
          onChange={setToken}
          placeholder="Paste the token"
          helper={`A token belongs to one ${noun}, the one above. We list its repositories when you connect. Stored encrypted, never shown again, never sent to the improver.`}
        />
        <Box sx={ROW_BOX_SX} aria-label="Scopes the token needs">
          {meta.readScopes.map((p, i) => (
            <Stack
              key={p.scope}
              direction="row"
              spacing={1.5}
              sx={{
                px: 1.5,
                py: 0.875,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{ ...T12, fontFamily: MONO, width: 128, flexShrink: 0 }}
              >
                {p.scope}
              </Typography>
              <Typography sx={{ ...T12, width: 40, flexShrink: 0 }}>
                {p.access}
              </Typography>
              <Typography
                sx={{ ...T12, color: "text.secondary", flex: 1, minWidth: 0 }}
              >
                {p.why}
              </Typography>
            </Stack>
          ))}
        </Box>
        <Stack direction="row" justifyContent="flex-end" spacing={1}>
          {onCancel && (
            <Button
              variant="outlined"
              disabled={connecting}
              onClick={onCancel}
              sx={OUTLINED_BTN_SX}
            >
              Cancel
            </Button>
          )}
          <Button
            variant="contained"
            color="primary"
            disabled={!token.trim() || connecting}
            onClick={() => setConnecting(true)}
            startIcon={
              connecting ? (
                <CircularProgress
                  size={12}
                  thickness={6}
                  sx={{ color: "inherit" }}
                />
              ) : (
                <Iconify icon={meta.icon} width={16} />
              )
            }
            sx={BTN_SX}
          >
            {connecting
              ? `Connecting to ${meta.label}…`
              : `Connect ${meta.label}`}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
GitHostConnect.propTypes = {
  host: PropTypes.oneOf(["gitlab", "bitbucket"]).isRequired,
  except: PropTypes.arrayOf(PropTypes.string),
  onConnected: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
};
