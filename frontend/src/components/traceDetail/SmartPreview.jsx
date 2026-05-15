import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Collapse, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import Markdown from "react-markdown";
import ChatMessageView, { isOpenAIMessages } from "./ChatMessageView";
import { getLlmData } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";

/**
 * SmartPreview — renders span preview differently based on observation type.
 * Falls back to ContentCard for unknown types.
 */
const SmartPreview = ({
  span,
  input,
  output,
  attributes,
  viewMode,
  searchQuery,
  ContentCard,
  AttributesCard,
  JsonPreviewBlock,
  drawerOpen = true,
}) => {
  const type = (span?.observation_type || "").toLowerCase();
  const model = span?.model;
  const provider = span?.provider;
  const providerLogo = span?.provider_logo;
  const status = span?.status;
  const statusMessage = span?.status_message;
  const promptTokens = span?.prompt_tokens;
  const completionTokens = span?.completion_tokens;
  const totalTokens = span?.total_tokens;
  const cost = span?.cost;
  const latency = span?.latency_ms ?? span?.latency;

  const isLLM = type === "llm" || type === "generation";
  const isTool = type === "tool";
  const isRetriever = type === "retriever";
  const isEmbedding = type === "embedding";
  const isGuardrail = type === "guardrail";
  const isError = status === "ERROR";

  // Extract LLM messages from span_attributes (OpenInference / GenAI format)
  // This is what the old drawer's DrawerRightRenderer did via getLlmData()
  const { inputMessages, outputMessages, hasAttrMessages } = useMemo(() => {
    if (!isLLM)
      return { inputMessages: [], outputMessages: [], hasAttrMessages: false };
    // Build a flat object that getLlmData expects: { input, output, span_attributes }
    const flatSpan = {
      input,
      output,
      span_attributes: attributes,
    };
    const inData = getLlmData(flatSpan, "input");
    const outData = getLlmData(flatSpan, "output");
    const inMsgs = inData?.inputMessage || [];
    const outMsgs = outData?.outputMessage || [];
    return {
      inputMessages: inMsgs,
      outputMessages: outMsgs,
      hasAttrMessages: inMsgs.length > 0 || outMsgs.length > 0,
    };
  }, [isLLM, input, output, attributes]);

  // Check for OpenAI messages format in raw input
  const hasMessages = isOpenAIMessages(input) || hasAttrMessages;

  // JSON mode always uses JsonPreviewBlock
  if (viewMode === "json") {
    return (
      <Stack spacing={1.5}>
        {isError && <ErrorBanner message={statusMessage} />}
        <JsonPreviewBlock
          span={span}
          input={input}
          output={output}
          attributes={attributes}
          searchQuery={searchQuery}
          hideInlineSearch
        />
      </Stack>
    );
  }

  // Chat mode (only for LLM with messages)
  if (viewMode === "chat" && isLLM && hasMessages) {
    return (
      <Stack spacing={1.5}>
        {isError && <ErrorBanner message={statusMessage} />}
        <ChatMessageView
          input={input}
          output={output}
          model={model}
          provider={provider}
          promptTokens={promptTokens}
          completionTokens={completionTokens}
          totalTokens={totalTokens}
          attrInputMessages={inputMessages}
          attrOutputMessages={outputMessages}
        />
        <AttributesCard
          attributes={attributes}
          searchQuery={searchQuery}
          hideInlineSearch
          spanId={span?.id}
          traceId={span?.trace}
          drawerOpen={drawerOpen}
        />
      </Stack>
    );
  }

  // Markdown mode with smart rendering
  return (
    <Stack spacing={1.5}>
      {isError && <ErrorBanner message={statusMessage} />}

      {/* LLM with messages → auto-detect and show chat view */}
      {isLLM && hasMessages ? (
        <ChatMessageView
          input={input}
          output={output}
          model={model}
          provider={provider}
          promptTokens={promptTokens}
          completionTokens={completionTokens}
          totalTokens={totalTokens}
          attrInputMessages={inputMessages}
          attrOutputMessages={outputMessages}
        />
      ) : isLLM ? (
        /* LLM without messages format — show model header + content cards */
        <>
          {model && (
            <ModelHeader
              model={model}
              provider={provider}
              providerLogo={providerLogo}
              tokens={totalTokens}
              cost={cost}
            />
          )}
          <ContentCard
            title="Input"
            content={input}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
          <ContentCard
            title="Output"
            content={output}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
        </>
      ) : isTool ? (
        /* Tool span — name + args + result */
        <>
          <ToolPreview
            name={span?.name}
            input={input}
            output={output}
            latency={latency}
            viewMode={viewMode}
            searchQuery={searchQuery}
            ContentCard={ContentCard}
          />
        </>
      ) : isRetriever ? (
        /* Retriever span — documents */
        <>
          <RetrieverPreview
            input={input}
            output={output}
            viewMode={viewMode}
            searchQuery={searchQuery}
            ContentCard={ContentCard}
          />
        </>
      ) : isEmbedding ? (
        /* Embedding span */
        <>
          {model && (
            <ModelHeader
              model={model}
              provider={provider}
              providerLogo={providerLogo}
              tokens={totalTokens}
            />
          )}
          <ContentCard
            title="Input"
            content={input}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
          <ContentCard
            title="Output"
            content={output}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
        </>
      ) : isGuardrail ? (
        /* Guardrail span — pass/fail badge */
        <>
          <GuardrailPreview
            status={status}
            input={input}
            output={output}
            viewMode={viewMode}
            searchQuery={searchQuery}
            ContentCard={ContentCard}
          />
        </>
      ) : type === "agent" || type === "chain" ? (
        /* Agent/Chain span — smart preview extracting key info */
        <AgentPreview
          span={span}
          input={input}
          output={output}
          model={model}
          provider={provider}
          providerLogo={providerLogo}
          tokens={totalTokens}
          cost={cost}
          latency={latency}
          attributes={attributes}
          viewMode={viewMode}
          searchQuery={searchQuery}
          ContentCard={ContentCard}
        />
      ) : (
        /* Default — I/O content cards */
        <>
          <ContentCard
            title="Input"
            content={input}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
          <ContentCard
            title="Output"
            content={output}
            viewMode={viewMode}
            searchQuery={searchQuery}
          />
        </>
      )}

      <AttributesCard
        attributes={attributes}
        searchQuery={searchQuery}
        hideInlineSearch
        spanId={span?.id}
        traceId={span?.trace}
        drawerOpen={drawerOpen}
      />
    </Stack>
  );
};

// ── Error Banner ──
const ErrorBanner = ({ message }) => (
  <Alert
    severity="error"
    variant="outlined"
    sx={{
      fontSize: 12,
      py: 0.5,
      "& .MuiAlert-message": { overflow: "hidden" },
    }}
  >
    <Typography sx={{ fontSize: 12, fontWeight: 600, mb: message ? 0.25 : 0 }}>
      Error
    </Typography>
    {message && (
      <Typography
        sx={{
          fontSize: 11,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          maxHeight: 120,
          overflow: "auto",
        }}
      >
        {message}
      </Typography>
    )}
  </Alert>
);

ErrorBanner.propTypes = { message: PropTypes.string };

// ── Model Header ──
const ModelHeader = ({ model, provider, providerLogo, tokens, cost }) => (
  <Box
    data-search-skip="true"
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1,
      flexWrap: "wrap",
      py: 0.5,
    }}
  >
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        px: 1,
        py: 0.25,
        bgcolor: "background.neutral",
        borderRadius: "4px",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      {providerLogo ? (
        <Box
          component="img"
          src={providerLogo}
          alt={provider}
          sx={{ width: 14, height: 14, borderRadius: "2px" }}
        />
      ) : (
        <Iconify icon="mdi:brain" width={14} sx={{ color: "text.secondary" }} />
      )}
      <Typography sx={{ fontSize: 12, fontWeight: 600, color: "text.primary" }}>
        {model}
      </Typography>
      {provider && (
        <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
          ({provider})
        </Typography>
      )}
    </Box>
    {tokens != null && tokens > 0 && (
      <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
        {formatTokenCount(tokens)} tokens
      </Typography>
    )}
    {cost != null && cost > 0 && (
      <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
        {formatCost(cost)}
      </Typography>
    )}
  </Box>
);

ModelHeader.propTypes = {
  model: PropTypes.string,
  provider: PropTypes.string,
  providerLogo: PropTypes.string,
  tokens: PropTypes.number,
  cost: PropTypes.number,
};

// ── Tool Preview ──
const ToolPreview = ({
  name,
  input,
  output,
  latency,
  viewMode,
  searchQuery,
  ContentCard,
}) => (
  <>
    <Box
      data-search-skip="true"
      sx={{ display: "flex", alignItems: "center", gap: 0.75 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.25,
          bgcolor: alpha("#EA580C", 0.08),
          borderRadius: "4px",
          border: "1px solid",
          borderColor: alpha("#EA580C", 0.2),
        }}
      >
        <Iconify
          icon="mdi:wrench-outline"
          width={14}
          sx={{ color: "#EA580C" }}
        />
        <Typography sx={{ fontSize: 13, fontWeight: 600, color: "#EA580C" }}>
          {name || "Tool"}
        </Typography>
      </Box>
      {latency != null && (
        <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
          {formatLatency(latency)}
        </Typography>
      )}
    </Box>
    <ContentCard
      title="Arguments"
      content={input}
      viewMode={viewMode}
      searchQuery={searchQuery}
    />
    <ContentCard
      title="Result"
      content={output}
      viewMode={viewMode}
      searchQuery={searchQuery}
    />
  </>
);

ToolPreview.propTypes = {
  name: PropTypes.string,
  input: PropTypes.any,
  output: PropTypes.any,
  latency: PropTypes.number,
  viewMode: PropTypes.string,
  searchQuery: PropTypes.string,
  ContentCard: PropTypes.elementType,
};

// ── Retriever Preview ──
const RetrieverPreview = ({
  input,
  output,
  viewMode,
  searchQuery,
  ContentCard,
}) => {
  // Try to parse output as documents array
  const docs = useMemo(() => {
    if (!output) return null;
    let parsed = output;
    if (typeof output === "string") {
      try {
        parsed = JSON.parse(output);
      } catch {
        return null;
      }
    }
    if (Array.isArray(parsed)) return parsed;
    return null;
  }, [output]);

  return (
    <>
      <ContentCard
        title="Query"
        content={input}
        viewMode={viewMode}
        searchQuery={searchQuery}
      />
      {docs ? (
        <Box>
          <Typography
            data-search-skip="true"
            sx={{ fontSize: 13, fontWeight: 500, mb: 0.75 }}
          >
            Retrieved Documents ({docs.length})
          </Typography>
          <Stack spacing={0.75}>
            {docs.slice(0, 10).map((doc, i) => (
              <Box
                key={i}
                sx={{
                  border: "1px solid",
                  borderColor: alpha("#0D9488", 0.2),
                  borderRadius: "4px",
                  bgcolor: alpha("#0D9488", 0.08),
                  p: 1,
                }}
              >
                <Box
                  data-search-skip="true"
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    mb: 0.25,
                  }}
                >
                  <Typography
                    sx={{ fontSize: 11, fontWeight: 600, color: "#0D9488" }}
                  >
                    Document {i + 1}
                  </Typography>
                  {doc.score != null && (
                    <Typography sx={{ fontSize: 11, color: "#0D9488" }}>
                      Score:{" "}
                      {typeof doc.score === "number"
                        ? doc.score.toFixed(3)
                        : doc.score}
                    </Typography>
                  )}
                </Box>
                <Typography
                  sx={{
                    fontSize: 12,
                    color: "text.primary",
                    whiteSpace: "pre-wrap",
                    maxHeight: 80,
                    overflow: "auto",
                  }}
                >
                  {doc.content ||
                    doc.text ||
                    doc.page_content ||
                    JSON.stringify(doc)}
                </Typography>
              </Box>
            ))}
            {docs.length > 10 && (
              <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
                +{docs.length - 10} more documents
              </Typography>
            )}
          </Stack>
        </Box>
      ) : (
        <ContentCard
          title="Output"
          content={output}
          viewMode={viewMode}
          searchQuery={searchQuery}
        />
      )}
    </>
  );
};

RetrieverPreview.propTypes = {
  input: PropTypes.any,
  output: PropTypes.any,
  viewMode: PropTypes.string,
  searchQuery: PropTypes.string,
  ContentCard: PropTypes.elementType,
};

// ── Guardrail Preview ──
const GuardrailPreview = ({
  status,
  input,
  output,
  viewMode,
  searchQuery,
  ContentCard,
}) => {
  const passed = status !== "ERROR";
  return (
    <>
      <Box
        data-search-skip="true"
        sx={{ display: "flex", alignItems: "center", gap: 0.75 }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            bgcolor: alpha(passed ? "#16A34A" : "#DC2626", 0.08),
            border: "1px solid",
            borderColor: alpha(passed ? "#16A34A" : "#DC2626", 0.2),
          }}
        >
          <Iconify
            icon={
              passed ? "mdi:shield-check-outline" : "mdi:shield-alert-outline"
            }
            width={14}
            sx={{ color: passed ? "#16A34A" : "#DC2626" }}
          />
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: passed ? "#16A34A" : "#DC2626",
            }}
          >
            {passed ? "Passed" : "Failed"}
          </Typography>
        </Box>
      </Box>
      <ContentCard
        title="Input"
        content={input}
        viewMode={viewMode}
        searchQuery={searchQuery}
      />
      <ContentCard
        title="Output"
        content={output}
        viewMode={viewMode}
        searchQuery={searchQuery}
      />
    </>
  );
};

GuardrailPreview.propTypes = {
  status: PropTypes.string,
  input: PropTypes.any,
  output: PropTypes.any,
  viewMode: PropTypes.string,
  searchQuery: PropTypes.string,
  ContentCard: PropTypes.elementType,
};

// ── Agent Preview ──
const AgentPreview = ({
  span,
  input,
  output,
  model,
  provider,
  providerLogo,
  tokens,
  cost,
  latency,
  attributes,
  viewMode,
  searchQuery,
  ContentCard,
}) => {
  // Extract agent-specific info from span name, attributes, and input
  const agentName = span?.name || "Agent";
  const attrs = attributes || {};

  // Try to extract user message / task from input
  const { userMessage, agentConfig } = useMemo(() => {
    if (!input) return { userMessage: null, agentConfig: null };
    let parsed = input;
    if (typeof parsed === "string") {
      try {
        parsed = JSON.parse(parsed);
      } catch {
        return { userMessage: input, agentConfig: null };
      }
    }
    if (typeof parsed !== "object")
      return { userMessage: String(parsed), agentConfig: null };

    // Gemini ADK: input has {model, config, contents}
    if (parsed.contents && Array.isArray(parsed.contents)) {
      const userMsgs = parsed.contents.filter((c) => c.role === "user");
      const lastUser = userMsgs[userMsgs.length - 1];
      const userText =
        lastUser?.parts?.map((p) => p.text || "").join("\n") || null;
      return {
        userMessage: userText,
        agentConfig: {
          model: parsed.model,
          tools: parsed.config?.tools?.length || 0,
        },
      };
    }

    // OpenAI Agents: input might be a messages array
    if (Array.isArray(parsed) && parsed[0]?.role) {
      const userMsgs = parsed.filter((m) => m.role === "user");
      const lastUser = userMsgs[userMsgs.length - 1];
      return { userMessage: lastUser?.content || null, agentConfig: null };
    }

    // Generic: look for common fields
    const msg =
      parsed.message ||
      parsed.query ||
      parsed.prompt ||
      parsed.input ||
      parsed.text;
    if (typeof msg === "string")
      return { userMessage: msg, agentConfig: parsed };

    return { userMessage: null, agentConfig: parsed };
  }, [input]);

  // Extract output text
  const outputText = useMemo(() => {
    if (!output) return null;
    let parsed = output;
    if (typeof parsed === "string") {
      try {
        parsed = JSON.parse(parsed);
      } catch {
        return output;
      }
    }
    if (typeof parsed !== "object") return String(parsed);
    // Gemini: {content: {role: "model", parts: [{text}]}}
    if (parsed.content?.parts)
      return parsed.content.parts.map((p) => p.text || "").join("\n");
    if (parsed.content?.role)
      return parsed.content.parts?.[0]?.text || JSON.stringify(parsed.content);
    // OpenAI: {role, content}
    if (parsed.content && typeof parsed.content === "string")
      return parsed.content;
    // Generic
    if (parsed.result)
      return typeof parsed.result === "string"
        ? parsed.result
        : JSON.stringify(parsed.result, null, 2);
    if (parsed.output)
      return typeof parsed.output === "string"
        ? parsed.output
        : JSON.stringify(parsed.output, null, 2);
    return null;
  }, [output]);

  // Collect tool names from attributes
  const toolNames = useMemo(() => {
    const tools = [];
    Object.keys(attrs).forEach((k) => {
      if (k.includes("tool") && k.includes("name")) tools.push(attrs[k]);
    });
    return tools;
  }, [attrs]);

  return (
    <>
      {/* Agent header badge */}
      <Box
        data-search-skip="true"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          flexWrap: "wrap",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            px: 1,
            py: 0.25,
            bgcolor: alpha("#9333EA", 0.08),
            borderRadius: "4px",
            border: "1px solid",
            borderColor: alpha("#9333EA", 0.2),
          }}
        >
          <Iconify
            icon="mdi:robot-outline"
            width={14}
            sx={{ color: "#9333EA" }}
          />
          <Typography sx={{ fontSize: 12, fontWeight: 600, color: "#9333EA" }}>
            {agentName}
          </Typography>
        </Box>
        {model && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              px: 1,
              py: 0.25,
              bgcolor: "background.neutral",
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {providerLogo ? (
              <Box
                component="img"
                src={providerLogo}
                alt={provider}
                sx={{ width: 13, height: 13, borderRadius: "2px" }}
              />
            ) : (
              <Iconify
                icon="mdi:brain"
                width={13}
                sx={{ color: "text.secondary" }}
              />
            )}
            <Typography
              sx={{ fontSize: 11, fontWeight: 500, color: "text.primary" }}
            >
              {model}
            </Typography>
          </Box>
        )}
        {tokens > 0 && (
          <Typography sx={{ fontSize: 10.5, color: "text.secondary" }}>
            {formatTokenCount(tokens)} tokens
          </Typography>
        )}
        {cost > 0 && (
          <Typography sx={{ fontSize: 10.5, color: "text.secondary" }}>
            {formatCost(cost)}
          </Typography>
        )}
        {latency > 0 && (
          <Typography sx={{ fontSize: 10.5, color: "text.secondary" }}>
            {formatLatency(latency)}
          </Typography>
        )}
      </Box>

      {/* Agent config summary (if detected) */}
      {agentConfig && (
        <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
          {agentConfig.tools > 0 && (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.25,
                px: 0.75,
                py: 0.15,
                bgcolor: alpha("#EA580C", 0.08),
                borderRadius: "3px",
                border: "1px solid",
                borderColor: alpha("#EA580C", 0.2),
              }}
            >
              <Iconify
                icon="mdi:wrench-outline"
                width={11}
                sx={{ color: "#EA580C" }}
              />
              <Typography sx={{ fontSize: 10, color: "#EA580C" }}>
                {agentConfig.tools} tools
              </Typography>
            </Box>
          )}
          {agentConfig.model && (
            <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
              Model: {agentConfig.model}
            </Typography>
          )}
        </Box>
      )}

      {/* User message / task */}
      {userMessage && (
        <ContentCard
          title="User Message"
          content={userMessage}
          viewMode="markdown"
          searchQuery={searchQuery}
        />
      )}

      {/* Full input (collapsed by default if we extracted a user message) */}
      <ContentCard
        title="Input"
        content={input}
        viewMode={viewMode}
        searchQuery={searchQuery}
      />

      {/* Output — show extracted text or full output */}
      {outputText ? (
        <ContentCard
          title="Output"
          content={outputText}
          viewMode="markdown"
          searchQuery={searchQuery}
        />
      ) : (
        <ContentCard
          title="Output"
          content={output}
          viewMode={viewMode}
          searchQuery={searchQuery}
        />
      )}
    </>
  );
};

AgentPreview.propTypes = {
  span: PropTypes.object,
  input: PropTypes.any,
  output: PropTypes.any,
  model: PropTypes.string,
  provider: PropTypes.string,
  providerLogo: PropTypes.string,
  tokens: PropTypes.number,
  cost: PropTypes.number,
  latency: PropTypes.number,
  attributes: PropTypes.object,
  viewMode: PropTypes.string,
  searchQuery: PropTypes.string,
  ContentCard: PropTypes.elementType,
};

SmartPreview.propTypes = {
  span: PropTypes.object,
  input: PropTypes.any,
  output: PropTypes.any,
  attributes: PropTypes.object,
  viewMode: PropTypes.string,
  searchQuery: PropTypes.string,
  ContentCard: PropTypes.elementType.isRequired,
  AttributesCard: PropTypes.elementType.isRequired,
  JsonPreviewBlock: PropTypes.elementType.isRequired,
  drawerOpen: PropTypes.bool,
};

export default React.memo(SmartPreview);
