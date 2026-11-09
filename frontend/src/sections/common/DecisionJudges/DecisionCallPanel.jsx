import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import KeysDrawer from "src/components/custom-model-dropdown/KeysDrawer";
import {
  FORMAT_LABEL,
  evalPathCodeOf,
  formatDecisionUsd,
} from "./decisionModels";

/** One probability as a labelled bar. */
function ProbBar({ label, p, top }) {
  return (
    <Stack direction="row" alignItems="center" gap={1}>
      <Typography
        typography="s3"
        noWrap
        sx={{ width: 140, fontWeight: top ? 600 : 400 }}
        title={label}
      >
        {label}
      </Typography>
      <Box
        sx={{
          flex: 1,
          height: 8,
          borderRadius: 4,
          bgcolor: "action.hover",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            width: `${Math.round(p * 100)}%`,
            height: "100%",
            bgcolor: top ? "primary.main" : "text.disabled",
          }}
        />
      </Box>
      <Typography
        typography="s3"
        sx={{
          width: 44,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {p.toFixed(2)}
      </Typography>
    </Stack>
  );
}
ProbBar.propTypes = {
  label: PropTypes.string.isRequired,
  p: PropTypes.number.isRequired,
  top: PropTypes.bool,
};

/**
 * What a decision judge's call did (W1): the model the vendor used, the
 * format and route, the answer with its per-label probabilities (when the
 * format gives them), tokens, cost at decision:<model> per-token pricing,
 * time and who is billed. A refused call shows the gateway's status and
 * code, and a missing key opens the keys drawer on that provider.
 */
export default function DecisionCallPanel({ decision, error }) {
  const [raw, setRaw] = useState(false);
  const [keysFor, setKeysFor] = useState(null);
  if (!decision) return null;
  const refused = error ?? decision.error ?? null;
  const probs = decision.probabilities;
  const legendOf = (k) =>
    decision.response?.answers?.q1?.legend?.[k] ?? decision.legend?.[k] ?? k;
  const entries = probs
    ? Object.entries(probs).map(([k, p]) => [
        decision.question_type === "score" ? `${k} · ${legendOf(k)}` : k,
        Number(p),
      ])
    : [];
  const topP = entries.length ? Math.max(...entries.map(([, p]) => p)) : null;
  const usage = decision.usage ?? {};
  return (
    <Box
      data-testid="decision-call-panel"
      sx={{
        border: "1px solid",
        borderColor: refused ? "error.light" : "divider",
        borderRadius: 1,
        p: 1.25,
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Iconify icon="mdi:scale-balance" width={16} />
        <Typography typography="s2" fontWeight="fontWeightSemiBold">
          {`Decision · ${decision.model}`}
        </Typography>
        <Typography typography="s3" color="text.secondary">
          {`${FORMAT_LABEL[decision.format] ?? decision.format} · POST ${decision.route}`}
        </Typography>
      </Stack>
      {refused ? (
        <Box data-testid="decision-refused">
          <Typography typography="s2" color="error.main">
            {`${refused.status} ${refused.code}`}
          </Typography>
          <Typography typography="s3">{refused.message}</Typography>
          {evalPathCodeOf(refused) ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="decision-eval-code"
            >
              {evalPathCodeOf(refused) === "JEV_KEY_MISSING"
                ? "Eval path code: JEV_KEY_MISSING (no key saved; raised before any gateway call)"
                : `Eval path code: ${evalPathCodeOf(refused)} (the vendor rejected the saved key)`}
            </Typography>
          ) : null}
          {refused.code === "decision_provider_not_configured" && (
            <Button
              size="small"
              sx={{ mt: 0.5 }}
              startIcon={<Iconify icon="mdi:key-plus" width={14} />}
              onClick={() =>
                setKeysFor({
                  model_name: decision.model.split("/").pop(),
                  providers: decision.provider,
                })
              }
            >
              Add your key (Settings &gt; AI Providers)
            </Button>
          )}
        </Box>
      ) : (
        <>
          <Typography typography="s3" color="text.secondary">
            {[
              decision.resolved_model &&
                `answered by ${decision.resolved_model}`,
              decision.question_type && `question ${decision.question_type}`,
              decision.choice != null && `answer ${decision.choice}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
          {entries.length > 0 ? (
            <Box data-testid="decision-probabilities">
              {entries.map(([label, p]) => (
                <ProbBar key={label} label={label} p={p} top={p === topP} />
              ))}
            </Box>
          ) : (
            <Typography typography="s3" color="text.secondary">
              {`${FORMAT_LABEL[decision.format]} returns the answer only, no probability.`}
            </Typography>
          )}
          <Typography
            typography="s3"
            data-testid="decision-usage"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {[
              `${usage.input_tokens ?? 0} input tokens`,
              `${usage.output_tokens ?? 0} output`,
              `${formatDecisionUsd(decision.cost_usd)} at ${decision.price_key} ($${decision.input_per_mtok} / 1M input)`,
              decision.latency_ms != null &&
                `${(decision.latency_ms / 1000).toFixed(2)}\u00a0s`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {`Billed to your ${decision.provider_name ?? decision.provider} key · 0 Future AGI credits`}
          </Typography>
        </>
      )}
      {(decision.request || decision.response) && (
        <Box>
          <Button
            size="small"
            onClick={() => setRaw((v) => !v)}
            startIcon={
              <Iconify
                icon={raw ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={14}
              />
            }
          >
            Request and response
          </Button>
          {raw && (
            <Box
              component="pre"
              data-testid="decision-raw"
              sx={{
                m: 0,
                p: 1,
                bgcolor: "background.neutral",
                borderRadius: 0.5,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 260,
                overflow: "auto",
              }}
            >
              {`POST ${decision.route}\n${JSON.stringify(decision.request, null, 2)}${
                decision.response
                  ? `\n\n200\n${JSON.stringify(decision.response, null, 2)}`
                  : refused
                    ? `\n\n${refused.status} ${JSON.stringify({ error: { code: refused.code, message: refused.message } }, null, 2)}`
                    : ""
              }`}
            </Box>
          )}
        </Box>
      )}
      <KeysDrawer
        open={Boolean(keysFor)}
        selectedModel={keysFor}
        onClose={() => setKeysFor(null)}
      />
    </Box>
  );
}

DecisionCallPanel.propTypes = {
  decision: PropTypes.object,
  error: PropTypes.object,
};
