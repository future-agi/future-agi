import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  FormControlLabel,
  Link,
  Switch,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { GatewayEmptyState } from "../../components/frame";
import CodeSnippet from "../CodeSnippet";
import { typeEndpoint } from "../catalogRows";
import {
  API_KEY_ENV,
  gatewayBaseUrl,
  gatewayExtrasSnippet,
  modelSnippets,
} from "../snippets";
import { CREATE_KEY_PATH, callTarget } from "./modelFacts";
import { CopyButton, Mono, Section } from "./Section";
import GatewaySnippetKey from "../../overview/GatewaySnippetKey";
import { fillGatewaySnippet } from "../../overview/personalGatewaySnippet";

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
  managedOffered = true,
  personalKey = "",
}) {
  const navigate = useNavigate();
  const [stream, setStream] = useState(false);
  const target = callTarget(row);
  const targetModel = target?.model;
  const snippets = useMemo(
    () =>
      targetModel
        ? modelSnippets(baseUrl, targetModel, { type: row.type, stream })
        : [],
    [baseUrl, targetModel, row.type, stream],
  );

  if (!target) {
    // The title is an h2, the level of the sold state's "Call this model".
    return (
      <GatewayEmptyState
        icon="catalog"
        title="No request to copy for this model"
        titleComponent="h2"
        description={`${row.accessReason} To call it, add a provider that serves it with your own key.`}
        actionLabel="Go to Providers"
        onAction={() => navigate(paths.dashboard.gateway.providers)}
      />
    );
  }

  const base = gatewayBaseUrl(baseUrl);
  const ownAlso =
    target.managed && row.ownRoutes.length ? row.ownRoutes[0] : null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Section
        title="Call this model"
        note="The gateway speaks the OpenAI API: point any OpenAI SDK at its base URL."
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
            <Mono>{base}</Mono>
            <CopyButton
              text={base}
              label="Copy base URL"
              message="Base URL copied"
            />
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

        {snippets.length ? (
          <>
            {row.type === "chat" && (
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={stream}
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
                code: fillGatewaySnippet(snippet.code, personalKey),
              }))}
              ariaLabel="Request language"
            />
          </>
        ) : (
          <Typography typography="s2">
            Send{" "}
            <Mono>
              POST {base}
              {typeEndpoint(row.type)}
            </Mono>{" "}
            with <Mono>&quot;model&quot;: &quot;{target.model}&quot;</Mono>.
          </Typography>
        )}
      </Section>

      {row.type === "chat" && (
        <Section
          title="Gateway headers"
          note="Tag a request for your logs and analytics, and read what the gateway did from the response headers."
        >
          <CodeSnippet
            snippets={[
              {
                key: "curl",
                label: "cURL",
                code: fillGatewaySnippet(
                  gatewayExtrasSnippet(baseUrl, target.model),
                  personalKey,
                ),
              },
            ]}
            ariaLabel="Headers example language"
          />
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
        <ModelApiContent {...props} personalKey={personalKey} />
      )}
    </GatewaySnippetKey>
  );
}

ModelApi.propTypes = ModelApiContent.propTypes = {
  gatewayId: PropTypes.string,
  personalKey: PropTypes.string,
  row: PropTypes.object.isRequired,
  baseUrl: PropTypes.string,
  managedOffered: PropTypes.bool,
};
