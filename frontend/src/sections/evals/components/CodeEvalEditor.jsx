import {
  Box,
  CircularProgress,
  IconButton,
  InputBase,
  MenuItem,
  Select,
  Tooltip,
  Typography,
  Button,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import CodeEditor from "./CodeEditor";

export const PYTHON_CODE_TEMPLATE = `from typing import Any

# Return: bool, float (0-1), dict with score+reason, or None to skip
def evaluate(
    input: Any,       # Input to the AI system
    output: Any,      # AI system's output
    expected: Any,    # Ground truth (may be None)
    context: dict,    # Mode-specific data (see below)
    **kwargs          # Additional mapped variables
):
    """
    context keys by mode:
      Dataset:    context["row"], context["dataset_name"]
      Tracing:    context["span"], context["trace"], context["spans"], context["session"]
      Simulation: context["transcript"], context["call_metrics"]
    """
    if expected is None:
        return None

    return {
        "score": 1.0 if output == expected else 0.0,
        "reason": "Exact match check",
    }`;

export const JS_CODE_TEMPLATE = `// Return: number (0-1), {score, reason?, metadata?}, or null to skip
function evaluate({ input, output, expected, context, ...kwargs }) {
  /**
   * context keys by mode:
   *   Dataset:    context.row, context.datasetName
   *   Tracing:    context.span, context.trace, context.spans, context.session
   *   Simulation: context.transcript, context.callMetrics
   */
  if (expected === null) return null;

  return {
    score: output === expected ? 1.0 : 0.0,
    reason: "Exact match check",
  };
}`;

// System prompt for Falcon AI to generate eval code
const AI_CODE_SYSTEM_PROMPT = `You are an expert code evaluator writer. Generate evaluation functions based on user descriptions.

The function signature is:

Python:
def evaluate(input, output, expected, context, **kwargs):
    # input: AI system input
    # output: AI system output
    # expected: ground truth (may be None)
    # context: dict with mode-specific data:
    #   Dataset mode:  context["row"] (all columns), context["dataset_name"]
    #   Tracing mode:  context["span"] (input/output/metadata/model/latency_ms/tokens),
    #                  context["trace"] (input/output/metadata/name),
    #                  context["spans"] (all spans list),
    #                  context["llm_spans"] (LLM-type spans only),
    #                  context["session"] (conversation history if available)
    #   Simulation:    context["transcript"], context["call_metrics"], context["call_status"]
    # **kwargs: additional mapped template variables
    #
    # Return: bool (pass/fail), float (0-1 score), dict {"score": float, "reason": str}, or None to skip

JavaScript:
function evaluate({ input, output, expected, context, ...kwargs }) {
  // Same structure, camelCase keys: context.span, context.trace, context.spans, etc.
  // Return: number (0-1), {score, reason?, metadata?}, or null to skip
}

Available safe imports (Python): json, re, math, collections, itertools, functools, string, datetime, decimal, statistics, copy, difflib, textwrap

IMPORTANT: Return ONLY the code. No markdown, no explanation, no backticks. Just the function code.`;

const CodeEvalEditor = ({
  code,
  setCode,
  codeLanguage,
  setCodeLanguage,
  datasetColumns = [],
  disabled = false,
}) => {
  const [aiMode, setAiMode] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalCode, setOriginalCode] = useState(null);
  const followUpRef = React.useRef(null);

  const handleLanguageChange = useCallback(
    (e) => {
      const newLang = e.target.value;
      setCodeLanguage(newLang);
      // Swap template if code is empty, matches a known template, or looks
      // like the default (starts with the template signature pattern).
      const trimmed = code.trim();
      const isDefaultPython =
        code === PYTHON_CODE_TEMPLATE ||
        trimmed.startsWith("from typing import Any") ||
        trimmed.startsWith("def evaluate(");
      const isDefaultJs =
        code === JS_CODE_TEMPLATE ||
        trimmed.startsWith("// Return:") ||
        trimmed.startsWith("function evaluate(");
      if (isDefaultPython || isDefaultJs || !trimmed) {
        setCode(newLang === "python" ? PYTHON_CODE_TEMPLATE : JS_CODE_TEMPLATE);
      }
    },
    [code, setCode, setCodeLanguage],
  );

  // Build context description for the AI
  const buildContext = useCallback(() => {
    const parts = [];
    if (datasetColumns.length > 0) {
      const colDetails = datasetColumns
        .filter((c) => c.name && !["id", "orgId"].includes(c.name))
        .map((c) => {
          const dt = c.dataType || "text";
          // Hint the LLM about what the data looks like based on type
          let hint = dt;
          if (dt === "image" || dt === "images")
            hint = "image URL (https://...)";
          else if (dt === "audio") hint = "audio URL (https://...)";
          else if (dt === "json") hint = "JSON object";
          else if (
            c.name.toLowerCase().includes("img") ||
            c.name.toLowerCase().includes("image")
          )
            hint = "image URL (https://...)";
          else if (
            c.name.toLowerCase().includes("url") ||
            c.name.toLowerCase().includes("link")
          )
            hint = "URL string";
          else if (
            c.name.toLowerCase().includes("caption") ||
            c.name.toLowerCase().includes("text")
          )
            hint = "text string";
          return `  - ${c.name}: ${hint}`;
        })
        .join("\n");
      parts.push(`Dataset columns:\n${colDetails}`);
      parts.push("");
      parts.push("Data access patterns:");
      parts.push('  - context["row"]["column_name"] — get any column value');
      parts.push('  - kwargs.get("column_name") — same data as keyword args');
      parts.push("");
      parts.push(
        "IMPORTANT: Image/audio columns contain URLs (https://...), NOT file paths.",
      );
      parts.push(
        "To check image size from URL, use: requests is blocked, so use len(value) for string size.",
      );
      parts.push(
        "To process images from URL, the data will be passed as URL strings.",
      );
    }
    parts.push(`Language: ${codeLanguage}`);
    parts.push(
      `Available imports: json, re, math, os.path, io, PIL, numpy, pandas, collections, typing, hashlib, base64, struct, csv, datetime, statistics, difflib`,
    );
    parts.push(
      `Blocked: subprocess, socket, requests, http, open(), os.system(), os.environ`,
    );
    if (code && code !== PYTHON_CODE_TEMPLATE && code !== JS_CODE_TEMPLATE) {
      parts.push(`\nCurrent code:\n${code}`);
    }
    return parts.join("\n");
  }, [datasetColumns, codeLanguage, code]);

  const handleAiGenerate = useCallback(
    async (prompt) => {
      if (!prompt?.trim()) return;
      setAiLoading(true);
      if (originalCode === null) setOriginalCode(code);

      try {
        const contextInfo = buildContext();
        const { data } = await axios.post(endpoints.develop.eval.aiEvalWriter, {
          description: `${AI_CODE_SYSTEM_PROMPT}\n\nContext:\n${contextInfo}\n\nUser request: ${prompt.trim()}`,
        });
        const raw = data?.result?.prompt;
        if (raw) {
          // Strip markdown code fences if present
          const cleaned = raw
            .replace(/^```(?:python|javascript|js)?\n?/i, "")
            .replace(/\n?```$/i, "")
            .trim();
          setCode(cleaned);
          setHasResult(true);
          setAiPrompt(prompt.trim());
          setTimeout(() => followUpRef.current?.focus(), 100);
        }
      } catch {
        // Fallback to local generation
        const desc = prompt.trim().toLowerCase();
        setCode(
          codeLanguage === "python"
            ? generatePythonEval(desc)
            : generateJsEval(desc),
        );
        setHasResult(true);
        setAiPrompt(prompt.trim());
      } finally {
        setAiLoading(false);
      }
    },
    [code, originalCode, codeLanguage, setCode, buildContext],
  );

  const handleAccept = useCallback(() => {
    setAiMode(false);
    setHasResult(false);
    setOriginalCode(null);
    setAiPrompt("");
  }, []);

  const handleReject = useCallback(() => {
    if (originalCode !== null) setCode(originalCode);
    setHasResult(false);
    setOriginalCode(null);
    setAiPrompt("");
  }, [originalCode, setCode]);

  const handleClose = useCallback(() => {
    if (hasResult && originalCode !== null) setCode(originalCode);
    setAiMode(false);
    setHasResult(false);
    setOriginalCode(null);
    setAiPrompt("");
  }, [hasResult, originalCode, setCode]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        position: "relative",
        zIndex: 1,
      }}
    >
      {/* Header: Code* label + language selector */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 0.5,
        }}
      >
        <Typography variant="body2" fontWeight={600}>
          Code*
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Select
            size="small"
            value={codeLanguage}
            onChange={handleLanguageChange}
            disabled={disabled}
            sx={{
              fontSize: "12px",
              height: 28,
              ...(disabled && { cursor: "not-allowed" }),
            }}
          >
            <MenuItem value="python">Python</MenuItem>
            <MenuItem value="javascript">JavaScript</MenuItem>
          </Select>
        </Box>
      </Box>

      {/* AI writer bar — shows above editor when active (hidden for system evals) */}
      {aiMode && !disabled && (
        <Box
          sx={{
            mb: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            backgroundColor: "background.aiSurface",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", px: 1.5, pt: 1 }}>
            {aiLoading ? (
              <Box
                sx={{ display: "flex", alignItems: "center", gap: 1, flex: 1 }}
              >
                <CircularProgress size={14} />
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontSize: "13px" }}
                >
                  Generating...
                </Typography>
              </Box>
            ) : !hasResult ? (
              <InputBase
                autoFocus
                fullWidth
                placeholder={
                  code &&
                  code !== PYTHON_CODE_TEMPLATE &&
                  code !== JS_CODE_TEMPLATE
                    ? "Describe changes — e.g. 'add JSON validation', 'make it stricter'"
                    : "Describe what to evaluate — e.g. 'check response has valid JSON'"
                }
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleAiGenerate(aiPrompt);
                  }
                  if (e.key === "Escape") handleClose();
                }}
                sx={{ fontSize: "13px", flex: 1 }}
              />
            ) : (
              <Typography
                variant="body2"
                sx={{
                  flex: 1,
                  fontSize: "13px",
                  color: "text.secondary",
                  fontStyle: "italic",
                }}
              >
                {aiPrompt}
              </Typography>
            )}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                ml: 1,
                flexShrink: 0,
              }}
            >
              {hasResult && (
                <>
                  <Button
                    size="small"
                    onClick={handleReject}
                    sx={{ color: "text.secondary" }}
                  >
                    Reject
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={handleAccept}
                  >
                    Accept
                  </Button>
                </>
              )}
              {!hasResult && !aiLoading && (
                <IconButton
                  size="small"
                  onClick={() => handleAiGenerate(aiPrompt)}
                  disabled={!aiPrompt.trim()}
                >
                  <SvgColor
                    src="/assets/icons/navbar/ic_falcon_ai.svg"
                    sx={{
                      width: 16,
                      height: 16,
                      color: aiPrompt.trim() ? "primary.main" : "text.disabled",
                    }}
                  />
                </IconButton>
              )}
              <IconButton size="small" onClick={handleClose}>
                <Iconify
                  icon="solar:close-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </Box>
          </Box>
          {hasResult && (
            <Box sx={{ px: 1.5, pb: 1, pt: 0.5 }}>
              <InputBase
                inputRef={followUpRef}
                fullWidth
                placeholder="Add a follow-up..."
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    e.target.value.trim()
                  ) {
                    e.preventDefault();
                    handleAiGenerate(e.target.value);
                    e.target.value = "";
                  }
                  if (e.key === "Escape") handleClose();
                }}
                sx={{
                  fontSize: "13px",
                  borderTop: "1px solid",
                  borderColor: "divider",
                  pt: 0.75,
                }}
              />
            </Box>
          )}
        </Box>
      )}

      {/* Monaco editor */}
      <Box sx={{ flex: 1, minHeight: 0, position: "relative" }}>
        <CodeEditor
          value={code}
          onChange={(val) => setCode(val || "")}
          language={codeLanguage}
          height="450px"
          placeholder={
            codeLanguage === "python" ? PYTHON_CODE_TEMPLATE : JS_CODE_TEMPLATE
          }
          disabled={disabled}
        />

        {/* Falcon AI button — floating bottom-right inside editor */}
        {!aiMode && !disabled && (
          <Tooltip
            title="Generate Eval code with Falcon AI"
            arrow
            placement="left"
          >
            <IconButton
              size="small"
              onClick={() => setAiMode(true)}
              sx={{
                position: "absolute",
                bottom: 12,
                right: 12,
                backgroundColor: "status.discovery.bg",
                "&:hover": {
                  backgroundColor: "status.discovery.bg",
                },
              }}
            >
              <SvgColor
                src="/assets/icons/navbar/ic_falcon_ai.svg"
                sx={{ width: 18, height: 18, color: "primary.main" }}
              />
            </IconButton>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
};

// --- Local code generation helpers (fallback when API unavailable) ---

function generatePythonEval(description) {
  if (description.includes("json")) {
    return `import json
from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    """Check if the output is valid JSON and matches expected structure."""
    try:
        parsed = json.loads(str(output)) if isinstance(output, str) else output
    except (json.JSONDecodeError, TypeError):
        return {"score": 0.0, "reason": "Output is not valid JSON"}

    if expected is None:
        return {"score": 1.0, "reason": "Output is valid JSON"}

    try:
        expected_parsed = json.loads(str(expected)) if isinstance(expected, str) else expected
    except (json.JSONDecodeError, TypeError):
        return {"score": 0.5, "reason": "Output is valid JSON but expected is not parseable"}

    if parsed == expected_parsed:
        return {"score": 1.0, "reason": "JSON output matches expected exactly"}

    # Check key overlap
    if isinstance(parsed, dict) and isinstance(expected_parsed, dict):
        common = set(parsed.keys()) & set(expected_parsed.keys())
        total = set(parsed.keys()) | set(expected_parsed.keys())
        score = len(common) / len(total) if total else 0.0
        return {"score": score, "reason": f"Key overlap: {len(common)}/{len(total)}"}

    return {"score": 0.0, "reason": "JSON structures differ"}`;
  }

  if (description.includes("length") || description.includes("word")) {
    return `from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    """Check output length and word count."""
    if not output:
        return {"score": 0.0, "reason": "Output is empty"}

    text = str(output)
    word_count = len(text.split())
    char_count = len(text)

    # Score based on reasonable length (50-500 words)
    if word_count < 10:
        score = 0.2
        reason = f"Too short: {word_count} words"
    elif word_count > 1000:
        score = 0.5
        reason = f"Very long: {word_count} words"
    else:
        score = min(1.0, word_count / 100)
        reason = f"Length OK: {word_count} words, {char_count} chars"

    return {"score": score, "reason": reason}`;
  }

  if (
    description.includes("similar") ||
    description.includes("match") ||
    description.includes("compare")
  ) {
    return `import difflib
from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    """Compare output similarity to expected using sequence matching."""
    if expected is None:
        return None

    output_str = str(output).strip().lower()
    expected_str = str(expected).strip().lower()

    if output_str == expected_str:
        return {"score": 1.0, "reason": "Exact match"}

    ratio = difflib.SequenceMatcher(None, output_str, expected_str).ratio()

    return {
        "score": round(ratio, 3),
        "reason": f"Similarity: {ratio:.1%}",
        "metadata": {"similarity_ratio": ratio},
    }`;
  }

  if (
    description.includes("span") ||
    description.includes("trace") ||
    description.includes("latency")
  ) {
    return `from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    """Evaluate based on trace/span context — check latency and span count."""
    span = context.get("span", {})
    spans = context.get("spans", [])
    llm_spans = context.get("llm_spans", [])

    latency = span.get("latency_ms", 0)
    total_tokens = span.get("tokens", 0)

    issues = []
    score = 1.0

    # Check latency
    if latency > 5000:
        score -= 0.3
        issues.append(f"High latency: {latency}ms")

    # Check token usage
    if total_tokens > 10000:
        score -= 0.2
        issues.append(f"High token usage: {total_tokens}")

    # Check span count
    if len(spans) > 20:
        score -= 0.1
        issues.append(f"Too many spans: {len(spans)}")

    reason = "; ".join(issues) if issues else "All checks passed"
    return {
        "score": max(0.0, score),
        "reason": reason,
        "metadata": {
            "latency_ms": latency,
            "total_tokens": total_tokens,
            "span_count": len(spans),
            "llm_span_count": len(llm_spans),
        },
    }`;
  }

  // Default
  return `from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    """${description || "Custom evaluation"}"""
    if expected is None:
        return None

    output_str = str(output).strip().lower()
    expected_str = str(expected).strip().lower()

    if output_str == expected_str:
        return {"score": 1.0, "reason": "Exact match"}

    # Check if expected is contained in output
    if expected_str in output_str:
        return {"score": 0.8, "reason": "Expected value found in output"}

    return {"score": 0.0, "reason": "No match found"}`;
}

function generateJsEval(description) {
  if (description.includes("json")) {
    return `function evaluate({ input, output, expected, context, ...kwargs }) {
  /** Check if output is valid JSON and matches expected */
  let parsed;
  try {
    parsed = typeof output === "string" ? JSON.parse(output) : output;
  } catch {
    return { score: 0, reason: "Output is not valid JSON" };
  }

  if (expected === null) return { score: 1, reason: "Valid JSON" };

  const exp = typeof expected === "string" ? JSON.parse(expected) : expected;
  if (JSON.stringify(parsed) === JSON.stringify(exp)) {
    return { score: 1, reason: "Exact JSON match" };
  }

  return { score: 0.5, reason: "Valid JSON but doesn't match expected" };
}`;
  }

  if (description.includes("span") || description.includes("trace")) {
    return `function evaluate({ input, output, expected, context, ...kwargs }) {
  /** Evaluate based on trace context — latency, token usage, span count */
  const span = context.span || {};
  const spans = context.spans || [];
  const llmSpans = context.llmSpans || [];

  let score = 1.0;
  const issues = [];

  if (span.latencyMs > 5000) { score -= 0.3; issues.push("High latency: " + span.latencyMs + "ms"); }
  if (span.tokens > 10000) { score -= 0.2; issues.push("High tokens: " + span.tokens); }
  if (spans.length > 20) { score -= 0.1; issues.push("Many spans: " + spans.length); }

  return {
    score: Math.max(0, score),
    reason: issues.length ? issues.join("; ") : "All checks passed",
    metadata: { latencyMs: span.latencyMs, spanCount: spans.length, llmSpanCount: llmSpans.length },
  };
}`;
  }

  // Default
  return `function evaluate({ input, output, expected, context, ...kwargs }) {
  /** ${description || "Custom evaluation"} */
  if (expected === null) return null;

  const out = String(output).trim().toLowerCase();
  const exp = String(expected).trim().toLowerCase();

  if (out === exp) return { score: 1, reason: "Exact match" };
  if (out.includes(exp)) return { score: 0.8, reason: "Expected found in output" };
  return { score: 0, reason: "No match" };
}`;
}

CodeEvalEditor.propTypes = {
  code: PropTypes.string,
  setCode: PropTypes.func.isRequired,
  codeLanguage: PropTypes.string.isRequired,
  setCodeLanguage: PropTypes.func.isRequired,
  datasetColumns: PropTypes.array,
  disabled: PropTypes.bool,
};

export default CodeEvalEditor;
