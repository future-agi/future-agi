import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Box,
  FormControlLabel,
  Link,
  MenuItem,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { GatewayEmptyState } from "../../components/frame";
import { EMPTY } from "../../utils/formatters";
import {
  BASE_URL_UNAVAILABLE,
  PLACEHOLDER_REQUEST_NOTE,
  snippetBaseUrl,
} from "../../utils/snippetBaseUrl";
import CodeSnippet from "../CodeSnippet";
import { TYPE_OPTIONS } from "../catalogRows";

import {
  API_KEY_ENV,
  gatewayExtrasSnippet,
  modelSnippets,
  nativeSnippets,
  routingSnippets,
} from "../snippets";
import { CREATE_KEY_PATH, callTarget } from "./modelFacts";
import { CopyButton, Mono, Section } from "./Section";
import GatewaySnippetKey from "../../overview/GatewaySnippetKey";
import { fillGatewaySnippet } from "../../overview/personalGatewaySnippet";

const REQUEST_KINDS = TYPE_OPTIONS.filter(({ value }) =>
  ["chat", "embeddings", "images", "rerank", "audio_speech", "audio_transcriptions"].includes(value),
);
const NATIVE_RECIPES = ["messages", "responses"];
const RECIPES = [
  { value: "chat", label: "Basic chat" },
  { value: "routing", label: "Routing and fallback" },
  { value: "messages", label: "Messages (instructional)" },
  { value: "responses", label: "Responses (instructional)" },
];

// Fill only the credential declaration, never matching text inside a prompt.
const withPersonalKey = (code, key) => code.split("\n").map((line) =>
  /^\s+(?:-H "Authorization: Bearer |api_key=|apiKey:)/.test(line)
    ? fillGatewaySnippet(line, key) : line,
).join("\n");

// The response headers a caller can read; on a deployment that offers no
// managed models every request is billed by its own key.
const responseHeaders = (managedOffered) => [
  ["x-agentcc-request-id", "the request's id in Logs"],
  [
    "x-agentcc-billing-mode",
    managedOffered
      ? "managed (Future AGI credits) or byok (own key)"
      : "byok (own key)",
  ],
  ["x-agentcc-provider", "the provider that served it"],
  ["x-agentcc-latency-ms", "time spent in the gateway and upstream"],
  ["x-agentcc-cost", "what the request cost, when it was billed"],
];

function ModelApiContent({
  row,
  baseUrl,
  hosted,
  deploymentModeConfirmed = false,
  retryDeploymentMode,
  managedOffered = true,
  personalKey = "",
  requestSnapshot,
}) {
  const navigate = useNavigate();
  const [stream, setStream] = useState(false);
  const [selectedKind, setSelectedKind] = useState("");
  const [recipe, setRecipe] = useState("chat");
  const providerAccessReady = (row.providerAccessState ?? "ready") === "ready";
  const target = callTarget(row);
  const targetModel = target?.model;
  // Older own-provider rows claim chat without catalog metadata. The flag is
  // authoritative even before the producer's type:"unknown" change is merged.
  const unknownType = row.uncatalogued || !row.type || row.type === "unknown";
  const requestKind = unknownType ? selectedKind : row.type;
  const managedNonChat = target?.managed && requestKind !== "chat";
  const activeRecipe = requestKind !== "chat" || (target?.managed && NATIVE_RECIPES.includes(recipe))
    ? "chat" : recipe;
  const nativeRecipe = NATIVE_RECIPES.includes(activeRecipe);
  const chatRecipe = requestKind === "chat" && !nativeRecipe;
  // A blocked or unverified model has no target, and the page's snapshot
  // starts null: both must reach the empty state below, not read messages.
  const request = chatRecipe && targetModel && requestSnapshot?.model === targetModel
    && Array.isArray(requestSnapshot?.messages) ? requestSnapshot : undefined;
  const streaming = request ? Boolean(request.stream) : stream;
  // The URL the requests are built on: the configured public URL on a hosted
  // deployment, the placeholder only on a confirmed self-hosted one, nothing
  // (and a note) until the mode and hosted URL are usable.
  const endpoint = snippetBaseUrl(baseUrl, { hosted, deploymentModeConfirmed });
  const snippets = useMemo(() => {
    if (!targetModel || !endpoint.url || managedNonChat) return [];
    if (nativeRecipe) return nativeSnippets(endpoint.url, targetModel, { api: activeRecipe });
    if (activeRecipe === "routing") return routingSnippets(endpoint.url, targetModel, { stream: streaming, request });
    return modelSnippets(endpoint.url, targetModel, { type: requestKind, stream: streaming, request });
  }, [endpoint.url, targetModel, requestKind, streaming, request, managedNonChat, nativeRecipe, activeRecipe]);

  if (!target) {
    // The title is an h2, the level of the sold state's "Call this model".
    return (
      <GatewayEmptyState
        icon="catalog"
        title={providerAccessReady ? "No request to copy for this model" : "Own-provider access is not verified"}
        titleComponent="h2"
        description={providerAccessReady
          ? `${row.accessReason || ""} To call it, add a provider that serves it with your own key.`
          : "Retry the provider read before using an own-key route. Retained model details do not confirm current access."}
        actionLabel={providerAccessReady ? "Go to Providers" : "Review Providers"}
        onAction={() => navigate(paths.dashboard.gateway.providers)}
      />
    );
  }

  const base = endpoint.url;
  const ownAlso =
    target.managed && providerAccessReady ? row.ownRoutes?.find((r) => r.pushed !== false) : null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Section
        title="Call this model"
        note="Use a gateway API key and the public base URL below. Copying an example does not send a request."
      >
        <Box
          sx={{
            display: "grid",
            gap: 1,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              sm: "140px minmax(0, 1fr)",
            },
            alignItems: "center",
          }}
        >
          <Typography typography="s2" color="text.secondary">
            Base URL
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              minWidth: 0,
            }}
          >
            <Mono>{base || EMPTY}</Mono>
            {endpoint.kind === "configured" && (
              <CopyButton
                text={base}
                label="Copy base URL"
                message="Base URL copied"
              />
            )}
          </Box>
          <Typography typography="s2" color="text.secondary">
            Model
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              minWidth: 0,
            }}
          >
            <Mono>{target.model}</Mono>
            <CopyButton
              text={target.model}
              label="Copy model ID"
              message="Model ID copied"
            />
          </Box>
          <Typography typography="s2" color="text.secondary">
            API key
          </Typography>
          <Typography typography="s2">
            Set <Mono>{API_KEY_ENV}</Mono> to a gateway API key.{" "}
            <Link component={RouterLink} href={CREATE_KEY_PATH}>
              Create key
            </Link>
          </Typography>
        </Box>

        {unknownType && (
          <>
            <Alert severity="info">
              <Typography typography="s2" fontWeight={600}>Type not reported</Typography>
              Your provider did not report a model type. Choose a request kind
              supported by the provider; compatibility has not been verified.
            </Alert>
            <TextField
              select
              size="small"
              label="Request kind"
              value={selectedKind}
              onChange={(event) => setSelectedKind(event.target.value)}
              sx={{ width: { xs: "100%", sm: 280 } }}
            >
              <MenuItem value="">Choose a request kind</MenuItem>
              {REQUEST_KINDS.filter(({ value }) => !target.managed || value === "chat").map(({ value, label }) => (
                <MenuItem key={value} value={value}>{label}</MenuItem>
              ))}
            </TextField>
          </>
        )}
        {requestKind === "chat" && (
          <TextField
            select
            size="small"
            label="Recipe"
            value={activeRecipe}
            onChange={(event) => setRecipe(event.target.value)}
            sx={{ width: { xs: "100%", sm: 280 } }}
          >
            {RECIPES.filter(({ value }) => !target.managed || !NATIVE_RECIPES.includes(value)).map(({ value, label }) => (
              <MenuItem key={value} value={value}>{label}</MenuItem>
            ))}
          </TextField>
        )}
        {nativeRecipe && (
          <Alert severity="info">
            <Typography typography="s2">
              Instructional example: use only if your own provider supports this API.
              Compatibility has not been verified for this model and route.
            </Typography>
            <Typography typography="s2">
              Simple text only, not an export of your submitted request. A registered
              endpoint does not guarantee support for native beta or tool fields;
              routing translation may refuse them. These examples are not for Future AGI credits.
            </Typography>
            <Typography typography="s2">
              {activeRecipe === "messages"
                ? "The Messages SDK uses the unversioned base URL and adds /v1/messages; max_tokens is required."
                : "The OpenAI Responses SDK uses the /v1 base URL and sends input, not chat messages."}
            </Typography>
          </Alert>
        )}
        {activeRecipe === "routing" && (
          <Alert severity="info">
            <Typography typography="s2">
              Replace YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL with a qualified model ID
              your key can call. models[] replaces the configured chain for this
              request; list at most five fallback IDs. Eligible hops are attempted
              on 429 or upstream 5xx failures, not guaranteed to succeed.
            </Typography>
            <Typography typography="s2">
              An explicit organization fallback_enabled: false disables caller chains
              too; omitting the switch preserves the gateway default.
            </Typography>
            <Typography typography="s2">
              provider.order and provider.only name upstream-provider slugs on
              aggregator routes, not your organization provider IDs. Direct routes
              match their gateway provider ID instead. Replace the slug placeholder
              for your route. only restricts eligibility; order ranks candidates;
              allow_fallbacks: false prevents using providers outside that order.
            </Typography>
            <Typography typography="s2">
              Provider preferences are route-dependent: unavailable endpoint data
              can produce provider_preference_unavailable; filters with no eligible
              route can produce no_provider_matches. These fields are top-level
              JSON (Python uses extra_body), not organization configuration.
            </Typography>
            <Typography typography="s2">
              Mixed-price-unit fallback parity is not supported. Model, access and
              billing restrictions still apply at every hop; no automatic success
              or change to provider configuration is implied.
            </Typography>
          </Alert>
        )}
        {target.managed && !row.managedOn && (
          <Alert severity="info">
            Future AGI credits are off for your organization, so the gateway
            refuses this request.
          </Alert>
        )}
        {ownAlso && (
          <Typography typography="s2" color="text.secondary">
            To use your {ownAlso.providerName} key instead, send model{" "}
            <Mono>{ownAlso.sendAs}</Mono>.
          </Typography>
        )}

        {endpoint.kind !== "configured" && (
          <Alert
            severity="info"
            action={
              !deploymentModeConfirmed && retryDeploymentMode ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => retryDeploymentMode()}
                >
                  Retry deployment mode
                </Button>
              ) : undefined
            }
          >
            {/* A confirmed self-hosted deployment with no public address gets
                the new-key dialog's note: nothing is missing from the
                console. */}
            {endpoint.kind === "placeholder"
              ? PLACEHOLDER_REQUEST_NOTE
              : BASE_URL_UNAVAILABLE}
          </Alert>
        )}
        {base && snippets.length > 0 && (
          <>
            {request && (
              <Typography typography="s2" color="text.secondary">
                Using your submitted request, including its sampling and streaming settings.
              </Typography>
            )}
            {chatRecipe && (
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={streaming}
                    disabled={Boolean(request)}
                    onChange={(e) => setStream(e.target.checked)}
                  />
                }
                label={
                  <Typography typography="s2">Stream the response</Typography>
                }
                sx={{ ml: 0 }}
              />
            )}
            <CodeSnippet
              snippets={snippets.map((snippet) => ({
                ...snippet,
                code: withPersonalKey(snippet.code, personalKey),
              }))}
              ariaLabel="Request language"
            />
            {chatRecipe && streaming && (
              <Typography typography="s2" color="text.secondary">
                Final usage is shown when the provider reports it. cURL prints raw
                stream events, including errors. Partial output is not a completed
                response: do not automatically replay it. Use the request ID in Logs
                to check the final outcome and cost.
              </Typography>
            )}
            {requestKind === "images" && (
              <Typography typography="s2" color="text.secondary">
                The response contains image data: use data[].url or decode
                data[].b64_json, depending on your provider.
              </Typography>
            )}
            {requestKind === "rerank" && (
              <Typography typography="s2" color="text.secondary">
                Read results[].index to identify the input document and
                results[].relevance_score for its score.
              </Typography>
            )}
            {requestKind === "audio_speech" && (
              <Typography typography="s2" color="text.secondary">
                Replace YOUR_VOICE with a voice your provider supports and edit input.
                The binary response is saved to speech.mp3, not printed as text.
                Confirm the provider supports MP3 output; inspect an HTTP error before
                treating the saved file as audio.
              </Typography>
            )}
            {requestKind === "audio_transcriptions" && (
              <Typography typography="s2" color="text.secondary">
                Replace YOUR_AUDIO_FILE.wav with your audio file (up to 25 MB).
                cURL sets the multipart boundary; do not add a JSON Content-Type.
                response_format=text requests plain text on stdout. If the provider
                instead returns JSON, read its text field; supported formats depend
                on the provider.
              </Typography>
            )}
          </>
        )}
        {base && !snippets.length && (
          <Typography typography="s2" color="text.secondary">
            {!requestKind
              ? "Choose a request kind to see an example. This does not test the model."
              : managedNonChat
                ? "Future AGI credits support chat requests only. Use your own provider for this request kind."
                : requestKind === "video"
                  ? "This gateway does not serve video generation. There is no request example to copy."
                  : "There is no request example for this type. Check your provider's API documentation for the required fields."}
          </Typography>
        )}
      </Section>

      {chatRecipe && activeRecipe === "chat" && (
        <Section
          title="Gateway headers"
          note="Tag a request for your logs and analytics, and read what the gateway did from the response headers."
        >
          {base && (
            <CodeSnippet
              snippets={[
                {
                  key: "curl",
                  label: "cURL",
                  code: withPersonalKey(
                    gatewayExtrasSnippet(base, target.model, { request }),
                    personalKey,
                  ),
                },
              ]}
              ariaLabel="Headers example language"
            />
          )}
          <Box component="ul" sx={{ m: 0, pl: 2.5, display: "grid", gap: 0.5 }}>
            <Typography component="li" typography="s2">
              <Mono>x-agentcc-metadata</Mono>: a JSON object of your own
              dimensions (feature, team, customer), recorded on the request log.
            </Typography>
            <Typography component="li" typography="s2">
              <Mono>x-agentcc-session-id</Mono>: groups requests into a session
              under Sessions.
            </Typography>
            {responseHeaders(managedOffered).map(([name, meaning]) => (
              <Typography key={name} component="li" typography="s2">
                Response <Mono>{name}</Mono>: {meaning}.
              </Typography>
            ))}
          </Box>
        </Section>
      )}
    </Box>
  );
}

export default function ModelApi({ gatewayId, ...props }) {
  if (!callTarget(props.row)) return <ModelApiContent {...props} />;
  return (
    <GatewaySnippetKey gatewayId={gatewayId}>
      {(personalKey) => (
        <ModelApiContent key={`${props.row.id}:${callTarget(props.row)?.model}`} {...props} personalKey={personalKey} />
      )}
    </GatewaySnippetKey>
  );
}

ModelApi.propTypes = ModelApiContent.propTypes = {
  gatewayId: PropTypes.string,
  personalKey: PropTypes.string,
  row: PropTypes.object.isRequired,
  baseUrl: PropTypes.string,
  hosted: PropTypes.bool,
  deploymentModeConfirmed: PropTypes.bool,
  retryDeploymentMode: PropTypes.func,
  managedOffered: PropTypes.bool,
  requestSnapshot: PropTypes.object,
};
