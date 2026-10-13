import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

const SCHEMA_VERSION = "omega-workforce-agent-draft/v1";
const MAX_MESSAGE_BYTES = 4000;
const MAX_FIELD_BYTES = {
  name: 200,
  role: 200,
  purpose: 4000,
  responsibility: 1000,
};

function byteLength(value) {
  return new TextEncoder().encode(value).length;
}

function authoredProposal(draft) {
  const proposal = {};
  for (const field of ["name", "role", "purpose"]) {
    if (typeof draft[field] === "string" && draft[field].trim())
      proposal[field] = draft[field].trim();
  }
  if (typeof draft.responsibilities === "string") {
    proposal.responsibilities =
      draft.sourceResponsibilities?.join("\n") === draft.responsibilities
        ? [...draft.sourceResponsibilities]
        : draft.responsibilities
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
  }
  return proposal;
}

function completeAuthoredProposal(draft) {
  const proposal = authoredProposal(draft);
  return proposal.name &&
    proposal.role &&
    proposal.purpose &&
    Array.isArray(proposal.responsibilities)
    ? {
        ...proposal,
        ...(Array.isArray(draft.examples) && draft.examples.length > 0
          ? { examples: draft.examples }
          : {}),
      }
    : null;
}

function parseProposal(result) {
  if (!result?.available)
    throw new Error(result?.reason || "The draft assistant is unavailable.");
  if (
    result.schemaVersion !== SCHEMA_VERSION ||
    result.reviewRequired !== true ||
    !result.proposal ||
    typeof result.proposal !== "object" ||
    Array.isArray(result.proposal)
  ) {
    throw new Error("The draft assistant returned an unsupported proposal.");
  }
  const source = result.proposal;
  if (
    byteLength(JSON.stringify(source)) > 8000 ||
    Object.keys(source).some(
      (key) =>
        ![
          "name",
          "role",
          "purpose",
          "responsibilities",
          "workMode",
          "connectorBindingIds",
          "examples",
        ].includes(key),
    ) ||
    source.workMode !== "on_demand" ||
    !Array.isArray(source.connectorBindingIds) ||
    source.connectorBindingIds.length !== 0
  ) {
    throw new Error("The draft assistant returned an unsupported proposal.");
  }
  const proposal = {};
  for (const field of ["name", "role", "purpose"]) {
    if (
      typeof source[field] !== "string" ||
      !source[field].trim() ||
      byteLength(source[field]) > MAX_FIELD_BYTES[field]
    ) {
      throw new Error("The draft assistant returned an incomplete proposal.");
    }
    proposal[field] = source[field].trim();
  }
  if (
    !Array.isArray(source.responsibilities) ||
    source.responsibilities.length > 64 ||
    source.responsibilities.some(
      (value) =>
        typeof value !== "string" ||
        !value.trim() ||
        byteLength(value) > MAX_FIELD_BYTES.responsibility,
    )
  ) {
    throw new Error("The draft assistant returned invalid responsibilities.");
  }
  proposal.responsibilities = source.responsibilities.map((value) =>
    value.trim(),
  );
  proposal.workMode = "on_demand";
  if (source.examples !== undefined) {
    if (
      !Array.isArray(source.examples) ||
      source.examples.length > 16 ||
      source.examples.some(
        (example) =>
          !example ||
          Object.keys(example).some(
            (key) => !["prompt", "response"].includes(key),
          ) ||
          typeof example.prompt !== "string" ||
          typeof example.response !== "string" ||
          !example.prompt.trim() ||
          !example.response.trim() ||
          byteLength(example.prompt) > 2000 ||
          byteLength(example.response) > 2000,
      )
    )
      throw new Error("The draft assistant returned invalid examples.");
    proposal.examples = source.examples.map((example) => ({
      prompt: example.prompt.trim(),
      response: example.response.trim(),
    }));
  }
  return proposal;
}

export default function AgentDraftAssistant({
  client,
  draft,
  onApply,
  disabled = false,
}) {
  const [message, setMessage] = useState("");
  const [proposal, setProposal] = useState(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const lifetime = useRef({ client });
  const requestRef = useRef(0);
  const retryRequest = useRef(null);
  const draftFingerprint = JSON.stringify({
    ...authoredProposal(draft),
    workMode: draft.workMode,
    examples: draft.examples,
  });
  const current = useRef({ client, disabled, draftFingerprint });
  current.current = { client, disabled, draftFingerprint };
  useEffect(() => {
    lifetime.current = { client };
    requestRef.current += 1;
    setPending(false);
    setMessage("");
    setError("");
    setProposal(null);
    retryRequest.current = null;
    return () => {
      requestRef.current += 1;
    };
  }, [client]);
  useEffect(() => {
    if (disabled) {
      requestRef.current += 1;
      setPending(false);
      setProposal(null);
    }
  }, [disabled]);

  const stale = proposal !== null && proposal.fingerprint !== draftFingerprint;
  const candidate = proposal?.definition;

  const generate = async () => {
    const prompt = message.trim();
    if (
      !prompt ||
      byteLength(prompt) > MAX_MESSAGE_BYTES ||
      pending ||
      disabled
    ) {
      if (byteLength(prompt) > MAX_MESSAGE_BYTES)
        setError("Describe the agent in 4,000 bytes or fewer.");
      return;
    }
    const requestId = ++requestRef.current;
    const scope = lifetime.current;
    const snapshot = draftFingerprint;
    const previous =
      candidate && !stale
        ? {
            name: candidate.name,
            role: candidate.role,
            purpose: candidate.purpose,
            responsibilities: candidate.responsibilities,
            ...(draft.examples?.length
              ? { examples: draft.examples }
              : candidate.examples?.length
                ? { examples: candidate.examples }
                : {}),
          }
        : completeAuthoredProposal(draft);
    const payload = {
      message: prompt,
      ...(previous ? { currentProposal: previous } : {}),
    };
    const payloadKey = JSON.stringify(payload);
    if (retryRequest.current?.payload !== payloadKey)
      retryRequest.current = {
        payload: payloadKey,
        key: `agent-draft-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`,
      };
    setPending(true);
    setError("");
    try {
      const response = await client.draftWorkforceAgent(
        payload,
        retryRequest.current.key,
      );
      if (
        requestId !== requestRef.current ||
        lifetime.current !== scope ||
        current.current.disabled
      )
        return;
      if (current.current.draftFingerprint !== snapshot) {
        setError(
          "Your draft changed while the assistant was working. Generate again to use your latest edits.",
        );
        return;
      }
      setProposal({
        definition: parseProposal(response),
        fingerprint: snapshot,
      });
      retryRequest.current = null;
    } catch (cause) {
      if (requestId === requestRef.current && lifetime.current === scope)
        setError(
          cause?.message ||
            "The draft assistant could not generate a proposal.",
        );
    } finally {
      if (requestId === requestRef.current && lifetime.current === scope)
        setPending(false);
    }
  };

  const apply = () => {
    if (!candidate || stale || pending || disabled) return;
    onApply({
      name: candidate.name,
      role: candidate.role,
      purpose: candidate.purpose,
      responsibilities: candidate.responsibilities.join("\n"),
      sourceResponsibilities: [...candidate.responsibilities],
      workMode: candidate.workMode,
      ...((!Array.isArray(draft.examples) || draft.examples.length === 0) &&
      Array.isArray(candidate.examples)
        ? { examples: candidate.examples }
        : {}),
      assistantProposal: true,
    });
    setProposal(null);
    setError("");
  };

  return (
    <Card
      variant="outlined"
      sx={{ borderColor: "secondary.main", bgcolor: "action.hover" }}
    >
      <CardContent>
        <Typography variant="subtitle1" fontWeight={600}>
          Describe your agent
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, mb: 1.5 }}
        >
          Ask the assistant for a starting definition. Review it below before it
          changes this draft; nothing is saved or deployed automatically.
        </Typography>
        <Stack spacing={1.25}>
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="What should this agent do?"
            value={message}
            disabled={disabled || pending}
            onChange={(event) => setMessage(event.target.value)}
            inputProps={{ maxLength: MAX_MESSAGE_BYTES }}
            placeholder="For example: monitor support feedback and produce a weekly summary with recurring issues."
          />
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              onClick={() => void generate()}
              disabled={disabled || pending || !message.trim()}
            >
              {pending
                ? "Generating…"
                : proposal
                  ? "Refine proposal"
                  : "Generate proposal"}
            </Button>
            {proposal ? (
              <Button
                variant="contained"
                onClick={apply}
                disabled={disabled || pending || stale}
              >
                Apply proposal
              </Button>
            ) : null}
          </Stack>
          {error ? <Alert severity="error">{error}</Alert> : null}
          {stale ? (
            <Alert severity="info">
              Your draft has changed. Generate a new proposal to keep those
              edits.
            </Alert>
          ) : null}
          {candidate ? (
            <Box
              component="section"
              aria-label="Draft assistant proposal"
              sx={{
                p: 1.5,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Typography variant="subtitle2">Review proposal</Typography>
              <Typography variant="body2" sx={{ mt: 0.75 }}>
                <strong>{candidate.name}</strong> · {candidate.role} · On demand
              </Typography>
              <Typography
                variant="body2"
                sx={{ mt: 0.75, whiteSpace: "pre-wrap" }}
              >
                {candidate.purpose}
              </Typography>
              <Typography
                variant="body2"
                component="ul"
                sx={{ mt: 0.75, mb: 0, pl: 2.5 }}
              >
                {candidate.responsibilities.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </Typography>
              {candidate.examples?.length ? (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 1 }}
                >
                  The proposal includes {candidate.examples.length} example
                  {candidate.examples.length === 1 ? "" : "s"}; existing
                  examples are preserved when you apply it.
                </Typography>
              ) : null}
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                Applying changes the authored draft only. Keep the selected
                environment, examples, and connections under your control.
              </Typography>
            </Box>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

AgentDraftAssistant.propTypes = {
  client: PropTypes.shape({ draftWorkforceAgent: PropTypes.func.isRequired })
    .isRequired,
  draft: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
