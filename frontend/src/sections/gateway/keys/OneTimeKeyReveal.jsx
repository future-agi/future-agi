import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { SegmentedTabs } from "src/components/tabs/tabs";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useManagedModelsStatus } from "../models/hooks/useManagedModels";
import { setupReadiness } from "../onboarding/setupReadiness";
import {
  API_KEY_ENV,
  configuredRouteCandidates,
  quickstartSnippets,
} from "../overview/quickstart";
import FaIcon from "../providers/ui/FaIcon";
import { GATEWAY_MONO } from "../utils/fonts";
import { permits } from "./keyRouteAccess";

async function copyText(text) {
  // navigator.clipboard is missing on plain-http hosts other than localhost.
  if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
  await navigator.clipboard.writeText(text);
}

export function Secret({ label, value, copyLabel }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await copyText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      enqueueSnackbar("Copy did not work here: select the text and copy it.", {
        variant: "warning",
      });
    }
  };
  return (
    <Box>
      <Typography typography="s2" color="text.secondary" mb={0.5}>
        {label}
      </Typography>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.neutral",
        }}
      >
        <Typography
          component="code"
          typography="s2_1"
          sx={{
            flex: 1,
            minWidth: 0,
            fontFamily: GATEWAY_MONO,
            overflowWrap: "anywhere",
            whiteSpace: "pre-wrap",
            userSelect: "all",
            color: "text.primary",
          }}
        >
          {value}
        </Typography>
        <Tooltip title={copied ? "Copied" : copyLabel}>
          <IconButton size="small" aria-label={copyLabel} onClick={handleCopy}>
            <FaIcon name={copied ? "check" : "copy"} />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

Secret.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  copyLabel: PropTypes.string.isRequired,
};

const shellQuote = (value) => `'${value.replace(/'/g, "'\\''")}'`;

function usableRequestUrl(value) {
  try {
    const url = new URL(value);
    return (
      ["https:", "http:"].includes(url.protocol) &&
      !url.username && !url.password && !url.search && !url.hash
    );
  } catch {
    return false;
  }
}

// Mounted only while the fresh key is on screen. Provider reads cannot create
// a key or send inference; failed reads never reuse cached route candidates.
function FirstRequest({ reveal, gatewayId, baseUrl, onDone }) {
  const query = useProviderHealth(gatewayId);
  const [selectedId, setSelectedId] = useState(null);
  const [language, setLanguage] = useState("curl");
  const panelId = useId();
  const source = query.data?.providers;
  const providers = Array.isArray(source)
    ? source
    : source && typeof source === "object"
      ? Object.entries(source).map(([name, provider]) => ({ ...provider, name }))
      : [];
  const failed = query.isError || Boolean(query.error);
  const routes = failed || query.isLoading ? [] : configuredRouteCandidates(providers);
  const candidates = routes.filter((route) =>
    permits(reveal.allowedModels, route.id) &&
    permits(reveal.allowedProviders, route.provider) &&
    permits(reveal.requestedModels ?? [], route.id) &&
    permits(reveal.requestedProviders ?? [], route.provider));
  // With no own-provider route at all, the next step depends on whether
  // Future AGI credits are ready; read that only when it is shown.
  const noOwnRoutes = !failed && !query.isLoading && !routes.length;
  const managedStatus = useManagedModelsStatus({ enabled: noOwnRoutes });
  const creditsReady = noOwnRoutes && !managedStatus.isError && !managedStatus.error &&
    setupReadiness({ managedStatus: managedStatus.data }).managed === "ready";
  const selected = selectedId === null
    ? candidates.find((route) => route.health === "healthy")
    : candidates.find((route) => route.id === selectedId);
  const snippets = selected && reveal.secret
    ? quickstartSnippets(baseUrl, selected.id)
    : [];
  const snippet = snippets.find((entry) => entry.key === language);
  const exportCommand = snippet
    ? `export ${API_KEY_ENV}=${shellQuote(reveal.secret)}`
    : "";
  // SDK source reads the environment; the secret stays in reveal-local shell
  // setup, never interpolated into Python/TypeScript or persisted elsewhere.
  const setup = snippet && language !== "curl"
    ? `${language === "python" ? "python -m pip install openai" : "npm install openai"}\n${exportCommand}`
    : "";
  const code = snippet
    ? language === "curl" ? `${exportCommand}\n\n${snippet.code}` : snippet.code
    : "";

  if (query.isLoading) {
    return <Typography typography="s2">Loading configured models…</Typography>;
  }
  if (failed) {
    return (
      <Alert
        severity="warning"
        action={
          <Button color="inherit" onClick={() => query.refetch()} disabled={query.isFetching}>
            Retry models
          </Button>
        }
      >
        Configured models could not be loaded. Your new key is still shown above.
      </Alert>
    );
  }
  if (!candidates.length) {
    return (
      <Stack spacing={1}>
        <Typography typography="s2" color="text.secondary">
          {routes.length
            ? "No configured route matches this key's access lists. Discover an allowed model before sending a request."
            : creditsReady
              ? "Your organization has no own-provider models ready yet. Pick a credit-eligible model on Models, or add a provider."
              : "Your organization has no own-provider models ready yet. Add and apply a provider, or discover models before sending a request."}
        </Typography>
        <Button
          component={RouterLink}
          href={paths.dashboard.gateway.models}
          onClick={onDone}
          sx={{ alignSelf: "flex-start" }}
        >
          Discover models
        </Button>
      </Stack>
    );
  }
  return (
    <Stack spacing={1.5}>
      <TextField
        select
        label="Model for first request"
        size="small"
        fullWidth
        SelectProps={{ native: true }}
        InputLabelProps={{ shrink: true }}
        value={selected?.id || ""}
        onChange={(event) => setSelectedId(event.target.value)}
      >
        <option value="">Choose a configured model</option>
        {candidates.map((route) => (
          <option key={route.id} value={route.id}>{route.id}</option>
        ))}
      </TextField>
      {selected && (
        <Typography typography="s2" color="text.secondary">
          {selected.readinessLabel}. {selected.compatibility === "unverified"
            ? "Chat-shaped example; model compatibility is unverified."
            : "Model metadata reports chat support."}
        </Typography>
      )}
      {snippet && (
        <>
          <SegmentedTabs
            value={language}
            onChange={(_, next) => setLanguage(next)}
            aria-label="Language"
          >
            {snippets.map((entry) => (
              <Tab
                key={entry.key}
                value={entry.key}
                label={entry.label}
                id={`${panelId}-${entry.key}`}
                aria-controls={panelId}
              />
            ))}
          </SegmentedTabs>
          <Stack
            key={`${language}-${selected.id}`}
            spacing={1.5}
            role="tabpanel"
            id={panelId}
            aria-labelledby={`${panelId}-${language}`}
            tabIndex={0}
          >
            {setup && (
              <>
                <Typography typography="s2" color="text.secondary">
                  Run setup in a POSIX shell (macOS/Linux). Keep that terminal open
                  for the request.
                  {language === "typescript" && " Node.js 22.18+ is required to run this TypeScript example directly."}
                </Typography>
                <Secret
                  label="Install and set your key"
                  value={setup}
                  copyLabel="Copy setup commands"
                />
              </>
            )}
            <Secret
              label={`First request (${snippet.label})`}
              value={code}
              copyLabel={`Copy ${snippet.label} request`}
            />
            {setup && (
              <Typography typography="s2" color="text.secondary">
                {language === "python"
                  ? "Save as first-request.py, then run python first-request.py in the same terminal."
                  : "Save as first-request.mts, then run node first-request.mts in the same terminal."}
              </Typography>
            )}
          </Stack>
          <Typography typography="s2" color="text.secondary">
            {setup ? "The setup" : "This command"} contains your secret and may stay
            in your shell history. Copying sends nothing; running the request may
            incur provider charges.
          </Typography>
        </>
      )}
    </Stack>
  );
}

FirstRequest.propTypes = {
  reveal: PropTypes.object.isRequired,
  gatewayId: PropTypes.string.isRequired,
  baseUrl: PropTypes.string.isRequired,
  onDone: PropTypes.func.isRequired,
};

// Escape and backdrop clicks never discard the show-once secret. Done or an
// explicit next-step navigation clears it through the same parent callback.
export default function OneTimeKeyReveal({
  reveal,
  baseUrl,
  requestBaseUrl,
  requestBaseUrlNote,
  gatewayId,
  onDone,
}) {
  const open = Boolean(reveal);
  const canBuildRequest = gatewayId && usableRequestUrl(requestBaseUrl);
  const gatewayKeyId = reveal?.gatewayKeyId;
  const logsHref = typeof gatewayKeyId === "string" && gatewayKeyId
    ? `${paths.dashboard.gateway.logs}?api_key_id=${encodeURIComponent(gatewayKeyId)}`
    : paths.dashboard.gateway.logs;
  return (
    <Dialog
      open={open}
      onClose={() => {}}
      disableEscapeKeyDown
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Copy your new key</DialogTitle>
      {reveal && (
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            <Typography typography="s1" color="text.secondary">
              This is the only time the secret for{" "}
              <strong>{reveal.name}</strong> is shown. Store it somewhere safe:
              it will not be shown again.
            </Typography>
            <Secret
              label="API key"
              value={reveal.secret}
              copyLabel="Copy key"
            />
            {baseUrl && (
              <Secret
                label="Base URL (OpenAI-compatible)"
                value={baseUrl}
                copyLabel="Copy base URL"
              />
            )}
            {requestBaseUrlNote && (
              <Alert severity="info">{requestBaseUrlNote}</Alert>
            )}
            {(reveal.notes || []).map((note) => (
              <Alert key={note.text} severity={note.severity}>
                {note.text}
              </Alert>
            ))}
            {canBuildRequest && reveal.secret ? (
              <FirstRequest
                reveal={reveal}
                gatewayId={gatewayId}
                baseUrl={requestBaseUrl}
                onDone={onDone}
              />
            ) : (
              <Typography typography="s2" color="text.secondary">
                A copy-ready request needs a confirmed gateway URL and an allowed
                model. Open request setup for endpoint and SDK instructions.
              </Typography>
            )}
            <Typography typography="s2" color="text.secondary">
              Copy your key before leaving. Opening setup, models or logs forgets
              the secret here.
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
              <Button
                component={RouterLink}
                href={paths.dashboard.gateway.overview}
                onClick={onDone}
              >
                Open request setup
              </Button>
              <Button component={RouterLink} href={logsHref} onClick={onDone}>
                {gatewayKeyId ? "View logs for this key" : "View logs"}
              </Button>
            </Stack>
          </Stack>
        </DialogContent>
      )}
      <DialogActions>
        <Button variant="contained" color="primary" onClick={onDone}>
          Done
        </Button>
      </DialogActions>
    </Dialog>
  );
}

OneTimeKeyReveal.propTypes = {
  reveal: PropTypes.shape({
    name: PropTypes.string.isRequired,
    secret: PropTypes.string.isRequired,
    id: PropTypes.string,
    gatewayKeyId: PropTypes.string,
    allowedModels: PropTypes.arrayOf(PropTypes.string),
    allowedProviders: PropTypes.arrayOf(PropTypes.string),
    requestedModels: PropTypes.arrayOf(PropTypes.string),
    requestedProviders: PropTypes.arrayOf(PropTypes.string),
    notes: PropTypes.arrayOf(
      PropTypes.shape({
        severity: PropTypes.oneOf(["info", "warning", "success"]).isRequired,
        text: PropTypes.string.isRequired,
      }),
    ),
  }),
  baseUrl: PropTypes.string,
  requestBaseUrl: PropTypes.string,
  // Said under the Base URL row when it and the request use the placeholder.
  requestBaseUrlNote: PropTypes.string,
  gatewayId: PropTypes.string,
  onDone: PropTypes.func.isRequired,
};
