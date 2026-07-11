import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import TextBlock from "./TextBlock";

function StatusIcon({ status }) {
  if (status === "running") {
    return (
      <CircularProgress
        size={12}
        thickness={6}
        sx={{ color: "text.disabled" }}
      />
    );
  }
  if (status === "completed") {
    return (
      <Iconify
        icon="mdi:check"
        width={13}
        sx={{ color: "success.main", flexShrink: 0 }}
      />
    );
  }
  if (status === "error") {
    return (
      <Iconify
        icon="mdi:close"
        width={13}
        sx={{ color: "error.main", flexShrink: 0 }}
      />
    );
  }
  if (status === "confirmation_required") {
    return (
      <Iconify
        icon="mdi:alert"
        width={13}
        sx={{ color: "warning.main", flexShrink: 0 }}
      />
    );
  }
  return null;
}

StatusIcon.propTypes = {
  status: PropTypes.string.isRequired,
};

// Compact uppercase chip marking write authority on the collapsed row —
// mutate gets a "write" badge, destructive an explicit "destructive" badge
// (UX_UI 7.1: mutations must be visually distinct from silent reads).
function PolicyBadge({ policy }) {
  if (policy !== "mutate" && policy !== "destructive") return null;
  const isDestructive = policy === "destructive";
  return (
    <Typography
      component="span"
      sx={{
        fontSize: 9,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        lineHeight: 1,
        px: 0.5,
        py: 0.25,
        borderRadius: "4px",
        flexShrink: 0,
        color: isDestructive ? "error.main" : "warning.dark",
        bgcolor: (theme) =>
          alpha(
            isDestructive
              ? theme.palette.error.main
              : theme.palette.warning.main,
            0.12,
          ),
      }}
    >
      {isDestructive ? "destructive" : "write"}
    </Typography>
  );
}

PolicyBadge.propTypes = {
  policy: PropTypes.string,
};

// Inline confirmation card (UX_UI 7.1) — part of the conversation record, not
// a modal. The Confirm button is the ONLY approver of a destructive action;
// it is bound to the exact tool + args previewed via a server-held token.
function ConfirmationBlock({ toolCall, label, onConfirmAction }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isStreaming = useFalconStore((s) => s.isStreaming);
  // Disable both buttons once a decision is sent — the server's
  // confirmation_resolved event flips the card to its final state.
  const [submitted, setSubmitted] = useState(false);

  const confirmation = toolCall.confirmation || {};
  const resolution = toolCall.confirmation_status || null;
  const preview = confirmation.preview;
  const undoNote = confirmation.undo_note;
  // "delete eval template" -> "Confirm delete"
  const verb = (label || "").split(" ")[0];
  const confirmLabel = verb ? `Confirm ${verb}` : "Confirm";

  const decide = (decision) => {
    if (!onConfirmAction || !confirmation.token) return;
    setSubmitted(true);
    onConfirmAction(confirmation.token, decision);
  };

  const resolved = {
    confirmed: {
      icon: "mdi:check-circle",
      color: "success.main",
      text: "Approved — Falcon is proceeding with this action.",
    },
    cancelled: {
      icon: "mdi:cancel",
      color: "text.disabled",
      text: "Cancelled — no action was taken.",
    },
    expired: {
      icon: "mdi:timer-off-outline",
      color: "text.disabled",
      text: "Confirmation expired — no action was taken. Ask Falcon again if you still want this.",
    },
  }[resolution];

  return (
    <Box
      sx={{
        ml: 1.25,
        mr: 0.75,
        mt: 0.25,
        mb: 0.75,
        p: 1.5,
        borderRadius: "8px",
        border: 1,
        borderColor: alpha(theme.palette.warning.main, resolved ? 0.2 : 0.45),
        bgcolor: alpha(theme.palette.warning.main, isDark ? 0.08 : 0.06),
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.75 }}>
        <Iconify
          icon="mdi:alert"
          width={15}
          sx={{ color: "warning.main", flexShrink: 0 }}
        />
        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
          Confirm destructive action
        </Typography>
      </Box>

      <Typography sx={{ fontSize: 12.5, color: "text.secondary", mb: 0.5 }}>
        Falcon wants to run:{" "}
        <Box
          component="span"
          sx={{
            fontFamily:
              "'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace",
            fontWeight: 600,
            color: "text.primary",
          }}
        >
          {label}
        </Box>
      </Typography>

      {preview && (
        <Typography
          sx={{
            fontSize: 12,
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            lineHeight: 1.55,
            mb: 0.75,
          }}
        >
          {preview}
        </Typography>
      )}

      <Typography
        sx={{
          fontSize: 11.5,
          fontWeight: 600,
          color: undoNote ? "text.secondary" : "error.main",
          mb: resolved ? 0 : 1.25,
        }}
      >
        {undoNote || "This cannot be undone."}
      </Typography>

      {resolved ? (
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 0.75, mt: 0.75 }}
        >
          <Iconify
            icon={resolved.icon}
            width={14}
            sx={{ color: resolved.color, flexShrink: 0 }}
          />
          <Typography sx={{ fontSize: 12, color: resolved.color }}>
            {resolved.text}
          </Typography>
        </Box>
      ) : (
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={submitted || isStreaming}
            onClick={() => decide("cancel")}
            sx={{ fontSize: 12, textTransform: "none" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={submitted || isStreaming}
            onClick={() => decide("confirm")}
            sx={{ fontSize: 12, textTransform: "none" }}
          >
            {confirmLabel}
          </Button>
        </Box>
      )}
    </Box>
  );
}

ConfirmationBlock.propTypes = {
  toolCall: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
  onConfirmAction: PropTypes.func,
};

// One short, clean line from a (possibly markdown) result summary — used as the
// inline hint on the collapsed row so the step reads in a single line instead
// of spilling into a wide block.
function firstLine(text) {
  if (!text) return "";
  const line =
    text
      .replace(/[#*`>|]/g, "")
      .split("\n")
      .map((s) => s.trim())
      .find(Boolean) || "";
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

export default function ToolCallCard({ toolCall, onConfirmAction }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const isDark = theme.palette.mode === "dark";

  // Support both snake_case (WebSocket streaming) and camelCase (API history)
  const tool_name = toolCall.tool_name;
  const tool_description = toolCall.tool_description;
  const params = toolCall.params;
  const status = toolCall.status;
  const result_summary = toolCall.result_summary;
  const result_full = toolCall.result_full;

  const isRunning = status === "running";
  const isError = status === "error";
  const isCompleted = status === "completed";
  // Destructive phase-1: the gate returned a preview instead of executing —
  // render the inline confirm card (always visible, not behind the chevron).
  const isConfirmation = status === "confirmation_required";
  const awaitingDecision = isConfirmation && !toolCall.confirmation_status;
  const canExpand = isCompleted || isError;

  // Keep the REAL tool name visible (just swap underscores for spaces) — the
  // exact name is information users rely on.
  const label = (tool_name || "tool").replace(/_/g, " ");
  const hint = firstLine(result_summary);

  const railColor = isDark
    ? alpha(theme.palette.common.white, 0.1)
    : alpha(theme.palette.common.black, 0.09);

  let railActiveColor = railColor;
  if (isRunning) railActiveColor = theme.palette.primary.main;
  if (awaitingDecision) railActiveColor = theme.palette.warning.main;

  return (
    <Box
      sx={{
        // A thin left rail makes consecutive tool steps read as one quiet
        // "working" group, visually subordinate to the answer text.
        borderLeft: "2px solid",
        borderColor: railActiveColor,
        ml: 0.25,
        my: 0.25,
      }}
    >
      {/* Collapsed header — a single compact line */}
      <Box
        onClick={() => canExpand && setExpanded((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          pl: 1.25,
          pr: 0.75,
          py: 0.5,
          borderRadius: "0 6px 6px 0",
          cursor: canExpand ? "pointer" : "default",
          userSelect: "none",
          transition: "background-color 0.15s ease",
          "&:hover": canExpand
            ? {
                bgcolor: isDark
                  ? alpha(theme.palette.common.white, 0.04)
                  : alpha(theme.palette.common.black, 0.03),
              }
            : undefined,
        }}
      >
        <StatusIcon status={status} />

        <Typography
          component="span"
          sx={{
            fontFamily:
              "'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace",
            fontWeight: 500,
            fontSize: 12,
            color: "text.secondary",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {label}
        </Typography>

        <PolicyBadge policy={toolCall.execution_policy} />

        {/* Inline one-line result hint (or running state) — truncates, never wraps */}
        {isRunning && (
          <Typography
            component="span"
            sx={{ fontSize: 11.5, color: "text.disabled", fontStyle: "italic" }}
          >
            running…
          </Typography>
        )}
        {isConfirmation && (
          <Typography
            component="span"
            sx={{
              fontSize: 11.5,
              color: awaitingDecision ? "warning.main" : "text.disabled",
              fontStyle: "italic",
            }}
          >
            {awaitingDecision
              ? "needs your confirmation"
              : toolCall.confirmation_status}
          </Typography>
        )}
        {!isRunning && !isConfirmation && !!hint && (
          <Typography
            component="span"
            title={hint}
            sx={{
              fontSize: 11.5,
              color: isError ? "error.main" : "text.disabled",
              flex: 1,
              minWidth: 0,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {hint}
          </Typography>
        )}

        {canExpand && (
          <Iconify
            icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={15}
            sx={{ color: "text.disabled", flexShrink: 0, ml: "auto" }}
          />
        )}
      </Box>

      {/* Inline confirmation card — always visible while the decision is
          pending; stays in the transcript as the audit record afterwards */}
      {isConfirmation && (
        <ConfirmationBlock
          toolCall={toolCall}
          label={label}
          onConfirmAction={onConfirmAction}
        />
      )}

      {/* Expanded details — params + result, contained and scrollable */}
      <Collapse in={expanded && canExpand} unmountOnExit>
        <Box sx={{ pl: 1.25, pr: 0.75, pb: 1, pt: 0.25 }}>
          {tool_description && (
            <Typography
              variant="caption"
              sx={{
                display: "block",
                mb: 1,
                fontSize: 11.5,
                color: "text.disabled",
                fontStyle: "italic",
                lineHeight: 1.5,
              }}
            >
              {tool_description}
            </Typography>
          )}

          {params && Object.keys(params).length > 0 && (
            <Box sx={{ mb: 1 }}>
              <Box
                onClick={(e) => {
                  e.stopPropagation();
                  setParamsExpanded((p) => !p);
                }}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  cursor: "pointer",
                  userSelect: "none",
                  mb: 0.5,
                }}
              >
                <Iconify
                  icon={
                    paramsExpanded ? "mdi:chevron-down" : "mdi:chevron-right"
                  }
                  width={13}
                  sx={{ color: "text.disabled" }}
                />
                <Typography
                  sx={{
                    fontSize: 10.5,
                    color: "text.disabled",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                  }}
                >
                  Parameters
                </Typography>
              </Box>
              <Collapse in={paramsExpanded}>
                <Box
                  component="pre"
                  sx={{
                    fontSize: 11.5,
                    fontFamily:
                      "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
                    bgcolor: isDark
                      ? alpha(theme.palette.common.white, 0.04)
                      : "grey.50",
                    color: "text.secondary",
                    borderRadius: "6px",
                    p: 1.25,
                    overflow: "auto",
                    maxHeight: 180,
                    m: 0,
                    border: 1,
                    borderColor: isDark
                      ? alpha(theme.palette.common.white, 0.06)
                      : alpha(theme.palette.common.black, 0.06),
                  }}
                >
                  {JSON.stringify(params, null, 2)}
                </Box>
              </Collapse>
            </Box>
          )}

          {(result_full || result_summary) && (
            <Box>
              <Typography
                sx={{
                  fontSize: 10.5,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  display: "block",
                  mb: 0.5,
                }}
              >
                Result
              </Typography>
              <Box
                sx={{
                  maxHeight: 280,
                  overflow: "auto",
                  borderRadius: "6px",
                  bgcolor: isDark
                    ? alpha(theme.palette.common.white, 0.04)
                    : "grey.50",
                  p: 1.25,
                  border: 1,
                  borderColor: isDark
                    ? alpha(theme.palette.common.white, 0.06)
                    : alpha(theme.palette.common.black, 0.06),
                }}
              >
                {result_full ? (
                  <TextBlock content={result_full} />
                ) : (
                  <Typography
                    sx={{
                      fontSize: 12,
                      color: isError ? "error.main" : "text.secondary",
                      lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {result_summary}
                  </Typography>
                )}
              </Box>
              {/* Truncation honesty — the agent caps result_full at 2,000
                  chars (agent.py result_text[:2000]); say so instead of
                  presenting a cut-off payload as if it were complete */}
              {result_full && result_full.length >= 2000 && (
                <Typography
                  sx={{
                    fontSize: 10.5,
                    color: "text.disabled",
                    fontStyle: "italic",
                    mt: 0.5,
                    display: "block",
                  }}
                >
                  Preview capped at 2,000 characters — the full result may be
                  longer. Falcon saw the complete output.
                </Typography>
              )}
            </Box>
          )}
        </Box>
      </Collapse>

      {/* Cheap undo — executed destructive legs with a paired compensating
          action surface a one-click prefill ("Undo" hydrates the chat input
          via pendingPrompt; sending stays a deliberate user act) */}
      {isCompleted && toolCall.undo && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            pl: 1.25,
            pr: 0.75,
            pb: 0.5,
          }}
        >
          <Iconify
            icon="mdi:undo-variant"
            width={13}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <Typography
            sx={{
              fontSize: 11.5,
              color: "text.disabled",
              flex: 1,
              minWidth: 0,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {toolCall.undo.note || "This action can be undone."}
          </Typography>
          <Button
            size="small"
            variant="text"
            color="inherit"
            onClick={() =>
              useFalconStore
                .getState()
                .setPendingPrompt(
                  toolCall.undo.prompt ||
                    `Undo the ${label} action you just performed, restoring things exactly as they were.`,
                )
            }
            sx={{
              fontSize: 11.5,
              textTransform: "none",
              py: 0,
              minWidth: 0,
              color: "text.secondary",
            }}
          >
            Undo
          </Button>
        </Box>
      )}
    </Box>
  );
}

ToolCallCard.propTypes = {
  toolCall: PropTypes.shape({
    call_id: PropTypes.string,
    tool_name: PropTypes.string,
    tool_description: PropTypes.string,
    params: PropTypes.object,
    status: PropTypes.string,
    execution_policy: PropTypes.string,
    confirmation: PropTypes.shape({
      token: PropTypes.string,
      tool_name: PropTypes.string,
      args: PropTypes.object,
      preview: PropTypes.string,
      expires_at: PropTypes.string,
      policy: PropTypes.string,
      undo_note: PropTypes.string,
    }),
    confirmation_status: PropTypes.oneOf(["confirmed", "cancelled", "expired"]),
    undo: PropTypes.object,
    result_summary: PropTypes.string,
    result_full: PropTypes.string,
    step: PropTypes.number,
  }).isRequired,
  onConfirmAction: PropTypes.func,
};
