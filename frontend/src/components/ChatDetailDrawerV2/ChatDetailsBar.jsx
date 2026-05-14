import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { fDateTime } from "src/utils/format-time";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import VoiceActionsDropdown, {
  VOICE_ACTIONS,
} from "src/components/VoiceDetailDrawerV2/VoiceActionsDropdown";

/**
 * Chat-specific top bar for the chat detail drawer. Renders the action
 * dropdown + a compact metric-chip strip, mirroring `CallDetailsBar` from
 * the voice drawer but only showing fields that make sense for chat
 * (no Phone, no Provider, no "Type: Inbound" since every chat is a text
 * session, no trace-tag editing for now — out of scope per TH-4530).
 */

/**
 * Responsive metric chip. The value cell gets a `min-width: 0` + ellipsis
 * so long strings (notably ended_reason, which is a free-form sentence)
 * truncate instead of blowing past the drawer's right edge. The max-width
 * scales with the drawer via container queries: wider drawer → longer
 * visible value. Full text is always available via tooltip on hover.
 */
const MetricChip = ({ label, value }) => {
  const fullText =
    typeof value === "string" || typeof value === "number"
      ? String(value)
      : "";
  return (
    <CustomTooltip
      show={!!fullText}
      title={fullText}
      arrow
      size="small"
      type="black"
      placement="top"
    >
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.25,
          bgcolor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "2px",
          minWidth: 64,
          // Chip itself shouldn't outgrow the parent row. Clamp to the
          // drawer width and let the parent flex-wrap break to a new
          // line once there's no room.
          maxWidth: "100%",
          fontSize: 11,
          color: "text.primary",
          lineHeight: "16px",
        }}
      >
        <Box component="span" sx={{ flexShrink: 0, whiteSpace: "nowrap" }}>
          {label} :
        </Box>
        <Box
          component="span"
          sx={{
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {value}
        </Box>
      </Box>
    </CustomTooltip>
  );
};

MetricChip.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
};

const formatDuration = (seconds) => {
  if (seconds == null) return null;
  const n = Number(seconds);
  if (!Number.isFinite(n)) return null;
  const m = Math.floor(n / 60);
  const s = Math.round(n % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
};

const formatMs = (ms) => {
  if (ms == null) return null;
  const n = Number(ms);
  if (!Number.isFinite(n) || n === 0) return null;
  return n < 1000 ? `${Math.round(n)}ms` : `${(n / 1000).toFixed(1)}s`;
};

const formatCost = (cost) => {
  if (cost == null) return null;
  const n = Number(cost);
  if (!Number.isFinite(n)) return null;
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
};

// Out of the voice action set, chat only wires Annotate + Download for now.
const CHAT_ACTIONS = VOICE_ACTIONS.filter((a) =>
  ["annotate", "download"].includes(a.id),
);

const ChatDetailsBar = ({ data, onAction }) => {
  const chips = useMemo(() => {
    const out = [];

    const status = data?.status;
    if (status) {
      out.push({
        label: "Status",
        value: String(status).replace(/_/g, " "),
      });
    }

    const duration = formatDuration(data?.duration_seconds ?? data?.duration);
    if (duration) out.push({ label: "Duration", value: duration });

    const avgLatency = formatMs(
      data?.avg_agent_latency_ms ?? data?.avg_latency_ms ?? data?.avg_latency,
    );
    if (avgLatency) out.push({ label: "Avg Latency", value: avgLatency });

    const turns = data?.turn_count ?? data?.turnCount;
    if (turns != null) out.push({ label: "Turns", value: String(turns) });

    const totalTokens = data?.total_tokens ?? data?.totalTokens;
    if (totalTokens != null) {
      out.push({ label: "Tokens", value: String(totalTokens) });
    }

    const cost = formatCost(data?.cost ?? data?.total_cost);
    if (cost) out.push({ label: "Cost", value: cost });

    const timestamp = data?.timestamp || data?.created_at;
    if (timestamp) out.push({ label: "When", value: fDateTime(timestamp) });

    if (data?.ended_reason) {
      out.push({
        label: "Ended",
        value: data.ended_reason,
      });
    }
    return out;
  }, [data]);

  if (chips.length === 0 && !onAction) return null;

  return (
    <Box
      sx={{
        px: 1.25,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.default",
        flexShrink: 0,
      }}
    >
      {onAction && (
        <Stack direction="row" justifyContent="flex-end" sx={{ mb: 0.75 }}>
          <VoiceActionsDropdown onAction={onAction} actions={CHAT_ACTIONS} />
        </Stack>
      )}

      {chips.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{
            flexWrap: "wrap",
            // Constrain the row to the bar's content width so children
            // with maxWidth: 100% have something to clamp against; lets
            // long chips (e.g. "Ended: ...") wrap to a new line instead
            // of overflowing horizontally.
            minWidth: 0,
            width: "100%",
          }}
        >
          {chips.map((c) => (
            <MetricChip key={c.label} label={c.label} value={c.value} />
          ))}
        </Stack>
      )}
    </Box>
  );
};

ChatDetailsBar.propTypes = {
  data: PropTypes.object,
  onAction: PropTypes.func,
};

export default ChatDetailsBar;
