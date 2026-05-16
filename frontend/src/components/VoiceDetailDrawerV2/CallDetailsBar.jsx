import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { enqueueSnackbar } from "notistack";
import { fDateTime } from "src/utils/format-time";
import TagChip from "src/components/traceDetail/TagChip";
import TagInput from "src/components/traceDetail/TagInput";
import { normalizeTags } from "src/components/traceDetail/tagUtils";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import VoiceActionsDropdown, { VOICE_ACTIONS } from "./VoiceActionsDropdown";

// Action ids that operate strictly on the trace record (and therefore
// no-op when the call isn't linked to a tracer trace).
const TRACE_GATED_ACTION_IDS = new Set(["tags", "dataset"]);

/**
 * Compact key:value chip — mirrors src/components/traceDetail/SpanDetailPane
 * MetricChip exactly (plain text, no Typography wrappers) so the voice
 * drawer looks identical to the trace drawer's metric row.
 */
const MetricChip = ({ label, value }) => (
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
      fontSize: 11,
      color: "text.primary",
      lineHeight: "16px",
      whiteSpace: "nowrap",
    }}
  >
    {label} : {value}
  </Box>
);

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

/**
 * Inline tags row with add/edit/remove — mirrors the trace drawer's
 * InlineTagsRow (src/components/traceDetail/SpanDetailPane.jsx). Voice
 * calls persist tags on the underlying trace record via the same endpoint
 * trace tags use.
 */
const InlineTagsRow = ({ tags = [], traceId }) => {
  const [isAdding, setIsAdding] = useState(false);
  const queryClient = useQueryClient();

  const normalized = useMemo(() => normalizeTags(tags), [tags]);

  const { mutate: saveTags, isPending } = useMutation({
    mutationFn: (newTags) =>
      axios.patch(apiPath("/tracer/trace/{id}/tags/", { id: traceId }), {
        tags: newTags,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voiceCallDetail"] });
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
    },
    onError: () => {
      enqueueSnackbar("Failed to update tags", { variant: "error" });
    },
  });

  const persist = useCallback((next) => saveTags(next), [saveTags]);

  return (
    <Stack
      direction="row"
      sx={{ flexWrap: "wrap", gap: 0.5, alignItems: "center" }}
    >
      <Iconify
        icon="mdi:tag-outline"
        width={13}
        sx={{ color: "text.disabled" }}
      />
      {normalized.map((tag, idx) => (
        <TagChip
          key={`${tag.name}-${idx}`}
          name={tag.name}
          color={tag.color}
          size="small"
          onRemove={() => persist(normalized.filter((_, i) => i !== idx))}
          onColorChange={(c) =>
            persist(
              normalized.map((t, i) => (i === idx ? { ...t, color: c } : t)),
            )
          }
          onRename={(n) => {
            if (normalized.some((t, i) => i !== idx && t.name === n)) return;
            persist(
              normalized.map((t, i) => (i === idx ? { ...t, name: n } : t)),
            );
          }}
        />
      ))}
      {isAdding ? (
        <Box
          sx={{ minWidth: 130 }}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setIsAdding(false);
          }}
        >
          <TagInput
            onAdd={(newTag) => {
              persist([...normalized, newTag]);
              setIsAdding(false);
            }}
            existingNames={normalized.map((t) => t.name)}
            disabled={isPending}
            placeholder="tag name"
          />
        </Box>
      ) : (
        <Box
          onClick={() => setIsAdding(true)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: "2px",
            px: 0.5,
            py: "1px",
            borderRadius: "3px",
            border: "1px dashed",
            borderColor: "divider",
            fontSize: 11,
            color: "text.disabled",
            cursor: "pointer",
            lineHeight: "16px",
            "&:hover": { borderColor: "primary.main", color: "primary.main" },
          }}
        >
          <Iconify icon="mdi:plus" width={12} />
          tag
        </Box>
      )}
    </Stack>
  );
};

InlineTagsRow.propTypes = {
  tags: PropTypes.array,
  traceId: PropTypes.string,
};

const CallDetailsBar = ({ data, onAction }) => {
  const chips = useMemo(() => {
    const out = [];
    const callType = data?.call_type;
    if (callType) out.push({ label: "Type", value: String(callType) });

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
      data?.avg_agent_latency_ms ?? data?.avg_agent_latency,
    );
    if (avgLatency) out.push({ label: "Avg Latency", value: avgLatency });

    if (data?.phone_number) {
      out.push({ label: "Phone", value: data.phone_number });
    }

    const provider = data?.provider || data?.call_metadata?.provider;
    if (provider) out.push({ label: "Provider", value: String(provider) });

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

  // Tags persist on the trace record, so we need the canonical trace_id
  // (not the call_execution_id that `data.id` carries in simulate mode).
  const traceId = data?.trace_id;
  // The voice call detail endpoint does not ship a `tags` field for
  // observe calls, so we fall back to fetching the canonical trace
  // record. Only do this for the observe (project) module — simulate
  // call IDs aren't real tracer traces and the endpoint 404s, which
  // surfaces a nasty "Unable to retrieve trace" toast.
  const isObserve = data?.module === "project";
  const { data: traceDetail } = useGetTraceDetail(isObserve ? traceId : null);
  const tags =
    traceDetail?.trace?.tags ||
    traceDetail?.tags ||
    data?.tags ||
    data?.trace?.tags ||
    [];

  if (chips.length === 0 && tags.length === 0 && !onAction && !traceId)
    return null;

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
      {/* Top row — Actions button top-right, like trace drawer. Actions
          that mutate the trace record ("Add tags", "Move to dataset") need a real tracer trace to exist
          for this call. Gate on the presence of a genuine `trace_id` on the
          data payload (distinct from the CallExecution id fallback)
          so the menu items aren't rendered as inert clicks. */}
      {onAction && (
        <Stack direction="row" justifyContent="flex-end" sx={{ mb: 0.75 }}>
          <VoiceActionsDropdown
            onAction={onAction}
            actions={
              data?.trace_id
                ? VOICE_ACTIONS
                : VOICE_ACTIONS.filter((a) => !TRACE_GATED_ACTION_IDS.has(a.id))
            }
          />
        </Stack>
      )}

      {/* Metric chips */}
      {chips.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ flexWrap: "wrap" }}
        >
          {chips.map((c) => (
            <MetricChip key={c.label} label={c.label} value={c.value} />
          ))}
        </Stack>
      )}

      {/* Inline tags row with "+ tag" — only when we have a traceId to
          persist against. Mirrors trace drawer InlineTagsRow exactly. */}
      {traceId && (
        <Box sx={{ mt: 0.75 }}>
          <InlineTagsRow tags={tags} traceId={traceId} />
        </Box>
      )}
    </Box>
  );
};

CallDetailsBar.propTypes = {
  data: PropTypes.object,
  onAction: PropTypes.func,
};

export default CallDetailsBar;
