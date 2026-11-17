import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { CODE_TAB, codeTabs } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";
import { newIdempotencyKey as sandboxKey } from "src/sections/omega/sandboxes/api/queries";
import { newIdempotencyKey as deploymentKey } from "src/sections/omega/deployments/api/queries";

import CodeIcon from "./CodeIcon";
import KeyStrip from "./KeyStrip";
import { copyText as copyToClipboard } from "./clipboard";
import {
  KEY_ENV,
  KEY_MODE,
  bodyText,
  requestFingerprint,
  toCurl,
} from "./curl";
import { createPanelKey, forgetKey, useHeldKey } from "./keyStore";
import { NO_RECORDING_CLASS } from "./noRecording";
import { sdkSnippet } from "./sdkSnippets";
import { currentScope, gatewayBaseUrl } from "./target";

const TAB_LABEL = {
  [CODE_TAB.CURL]: "curl",
  [CODE_TAB.PYTHON]: "Python",
  [CODE_TAB.TYPESCRIPT]: "TypeScript",
};

const freshKey = (kind) =>
  kind === "sandbox" ? sandboxKey() : deploymentKey(kind);

/** Save `text` as `name` from the browser (the request body a deploy snippet reads). */
function download(name, text) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * The calls a page can make, as code a person can copy: curl always, the
 * Python and TypeScript tabs once their package is published (P22's switch).
 * The snippet carries the personal API key masked; Copy carries it only after
 * an explicit reveal, and otherwise reads it from the shell.
 *
 * `idempotencyKey` is the page's own key for a keyed request, when the page
 * holds one before its button is pressed (the deployment wizard keeps one per
 * distinct request): the snippet then sends that very key, so running it and
 * pressing the button deploy once. Otherwise each distinct request (method,
 * route and body: requestFingerprint) gets its own key, in the page's format,
 * kept while the panel is open.
 *
 * Nothing that can show the key in full is ever session-recorded
 * (NO_RECORDING_CLASS on the snippet, the key strip and Copy's fallback).
 */
export default function CodePanel({
  requests,
  idempotencyKey: pageKey = null,
  testId = "code-panel",
}) {
  const python = useOmegaPart(OMEGA_PART.SDK_PYTHON);
  const typescript = useOmegaPart(OMEGA_PART.SDK_TYPESCRIPT);
  const released = {
    [OMEGA_PART.SDK_PYTHON]: python,
    [OMEGA_PART.SDK_TYPESCRIPT]: typescript,
  };
  const scope = currentScope();
  const baseUrl = gatewayBaseUrl();
  // The key is the signed-in person's own, in this organization: nobody else
  // who signs in to this tab sees it (keyStore.js).
  const userId = useSafeAuthContext().user?.id ?? null;
  const heldKey = useHeldKey({ organizationId: scope.organizationId, userId });
  const [selected, setSelected] = useState(requests[0]?.id ?? null);
  const [tab, setTab] = useState(CODE_TAB.CURL);
  const [revealed, setRevealed] = useState(false);
  // "copied" for a moment after a copy; "failed" until the next attempt.
  const [copyState, setCopyState] = useState(null);
  const [creating, setCreating] = useState(false);
  const [createFailed, setCreateFailed] = useState(false);
  const root = useRef(null);
  // One Idempotency-Key per distinct request while this panel is open, keyed
  // by the request itself, not by which call it is: a rollback that now names
  // another revision (the page re-read the deployment) is another request,
  // and EE refuses an old key with it (409 idempotency_conflict). The same
  // request copied and run twice sends the same key, which the deployment
  // routes replay. The sandbox routes read no Idempotency-Key at EE 2be31e97
  // (openapi policy/sandboxes.ts): the header is sent as the page sends it,
  // and running a create or snapshot command again makes another one.
  const keys = useRef(new Map());

  useEffect(() => {
    if (copyState !== "copied") return undefined;
    const timer = setTimeout(() => setCopyState(null), 1600);
    return () => clearTimeout(timer);
  }, [copyState]);
  useEffect(() => {
    if (!heldKey) setRevealed(false);
  }, [heldKey]);

  const request =
    requests.find((one) => one.id === selected) ?? requests[0] ?? null;
  if (!request) return null;

  const sdkOptions = { baseUrl, scope, released };
  const tabs = codeTabs({ python, typescript }).filter(
    (one) =>
      one === CODE_TAB.CURL || sdkSnippet(one, request, sdkOptions) !== null,
  );
  const shownTab = tabs.includes(tab) ? tab : CODE_TAB.CURL;

  let idempotencyKey;
  if (request.idempotent && pageKey) {
    idempotencyKey = pageKey;
  } else if (request.idempotent) {
    const fingerprint = requestFingerprint(request);
    if (!keys.current.has(fingerprint))
      keys.current.set(fingerprint, freshKey(request.idempotent));
    idempotencyKey = keys.current.get(fingerprint);
  }
  const curlFor = (mode) =>
    toCurl(request, { baseUrl, key: heldKey, mode, scope, idempotencyKey });

  const isCurl = shownTab === CODE_TAB.CURL;
  const masked = isCurl && Boolean(heldKey) && !revealed;
  const shown = isCurl
    ? curlFor(
        heldKey ? (revealed ? KEY_MODE.FULL : KEY_MODE.MASKED) : KEY_MODE.ENV,
      )
    : sdkSnippet(shownTab, request, sdkOptions);
  const copyText = () =>
    isCurl
      ? curlFor(heldKey && revealed ? KEY_MODE.FULL : KEY_MODE.ENV)
      : sdkSnippet(shownTab, request, sdkOptions);

  const copy = async () => {
    setCopyState(null);
    const done = await copyToClipboard(copyText(), root.current);
    setCopyState(done ? "copied" : "failed");
  };
  const copied = copyState === "copied";

  const create = async () => {
    setCreating(true);
    setCreateFailed(false);
    try {
      await createPanelKey({ organizationId: scope.organizationId, userId });
    } catch {
      setCreateFailed(true);
    } finally {
      setCreating(false);
    }
  };

  const envNote = `$${KEY_ENV.API} and $${KEY_ENV.SECRET}`;
  // Selected by hand, a masked command carries the dots, not the key: say how
  // to get one that runs.
  const copyFailed = masked
    ? "This browser did not let the page copy, and the command above shows your key masked, so as selected it would not run. Reveal the key, then select the command and copy it yourself."
    : "This browser did not let the page copy. Select the command above and copy it yourself.";
  let caption = `Copy reads the key from ${envNote}.`;
  if (isCurl && heldKey && revealed) caption = "Copy includes the key itself.";
  else if (isCurl && heldKey)
    caption = `Copy reads the key from ${envNote}. Reveal the key to copy it in full.`;
  else if (!isCurl) caption = `Reads ${envNote} from the environment.`;

  return (
    <Box
      ref={root}
      data-testid={testId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: { xs: "stretch", sm: "center" },
          flexDirection: { xs: "column", sm: "row" },
          gap: 1,
        }}
      >
        {requests.length > 1 ? (
          <TextField
            select
            size="small"
            label="Call"
            value={request.id}
            onChange={(event) => setSelected(event.target.value)}
            sx={{ flex: 1, minWidth: 0 }}
            inputProps={{ "data-testid": "code-request" }}
          >
            {requests.map((one) => (
              <MenuItem key={one.id} value={one.id}>
                {one.title}
              </MenuItem>
            ))}
          </TextField>
        ) : (
          <Typography
            typography="s1"
            fontWeight={500}
            color="text.primary"
            sx={{ flex: 1 }}
            data-testid="code-request-title"
          >
            {request.title}
          </Typography>
        )}
        {tabs.length > 1 && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={shownTab}
            onChange={(_, value) => value && setTab(value)}
            aria-label="Language"
          >
            {tabs.map((one) => (
              <ToggleButton
                key={one}
                value={one}
                data-testid={`code-tab-${one}`}
                sx={{ px: 1.5, py: 0.25, textTransform: "none" }}
              >
                {TAB_LABEL[one]}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
      </Box>

      {isCurl && (
        <KeyStrip
          heldKey={heldKey}
          revealed={revealed}
          onToggleReveal={() => setRevealed((value) => !value)}
          onForget={() => forgetKey()}
          onCreate={create}
          creating={creating}
          createFailed={createFailed}
        />
      )}

      <Box
        component="pre"
        className={NO_RECORDING_CLASS}
        aria-label={`${TAB_LABEL[shownTab]} for ${request.title}`}
        data-testid="code-snippet"
        data-request-id={request.id}
        data-tab={shownTab}
        sx={{
          m: 0,
          p: 1.5,
          maxHeight: 360,
          overflow: "auto",
          borderRadius: 0.75,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
          color: "text.primary",
          fontFamily: "monospace",
          fontSize: 12.5,
          lineHeight: 1.6,
          whiteSpace: "pre",
        }}
      >
        {shown}
      </Box>

      {request.destructive && (
        <Typography
          typography="s3"
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            // Readable on both themes: warning.main is pale on white.
            color: (theme) =>
              theme.palette.mode === "dark"
                ? theme.palette.warning.main
                : theme.palette.warning.icon,
          }}
          data-testid="code-destructive"
        >
          <CodeIcon name="alert" size={14} />
          Running it acts at once, as the page&apos;s own button does.
        </Typography>
      )}

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 1,
        }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ minWidth: 0 }}
          data-testid="code-copy-note"
        >
          {caption}
        </Typography>
        <Box sx={{ display: "flex", gap: 1, ml: "auto" }}>
          {isCurl && request.bodyFile && (
            <Button
              size="small"
              color="inherit"
              variant="outlined"
              startIcon={<CodeIcon name="download" />}
              onClick={() => download(request.bodyFile, bodyText(request))}
              data-testid="code-download-body"
            >
              {request.bodyFile}
            </Button>
          )}
          <Button
            size="small"
            variant="contained"
            startIcon={<CodeIcon name={copied ? "check" : "copy"} />}
            onClick={copy}
            data-testid="code-copy"
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </Box>
      </Box>
      {copyState === "failed" && (
        <Typography
          typography="s3"
          color="error.main"
          role="alert"
          data-testid="code-copy-failed"
        >
          {copyFailed}
        </Typography>
      )}
    </Box>
  );
}

CodePanel.propTypes = {
  requests: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      method: PropTypes.string.isRequired,
      path: PropTypes.string.isRequired,
    }),
  ).isRequired,
  idempotencyKey: PropTypes.string,
  testId: PropTypes.string,
};
