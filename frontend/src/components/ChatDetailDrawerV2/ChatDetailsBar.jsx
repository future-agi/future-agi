import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import useSaveEntityTags, {
  useLatestEntityTags,
  isSourceTag,
  tagUpdateErrorMessage,
} from "src/components/traceDetail/useSaveEntityTags";
import { fDateTime } from "src/utils/format-time";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import Iconify from "src/components/iconify";
import TagChip from "src/components/traceDetail/TagChip";
import TagInput from "src/components/traceDetail/TagInput";
import { normalizeTags } from "src/components/traceDetail/tagUtils";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import VoiceActionsDropdown, {
  VOICE_ACTIONS,
} from "src/components/VoiceDetailDrawerV2/VoiceActionsDropdown";
import { DRAWER_MODULE, TAG_INVALIDATION_QUERY_KEYS } from "./constants";

// Chat top bar — chip strip + actions dropdown + inline tags row.
// Mirrors the voice CallDetailsBar, minus voice-only fields.

// Action ids that operate on the trace record (and therefore require
// `data.trace_id` to be a real tracer trace — CallExecution ids would
// 404). Mirrors the gating in `VoiceDetailDrawerV2/CallDetailsBar.jsx`.
const TRACE_GATED_ACTION_IDS = new Set(["tags", "dataset"]);

/**
 * Responsive metric chip. The value cell gets a `min-width: 0` + ellipsis
 * so long strings (notably ended_reason, which is a free-form sentence)
 * truncate instead of blowing past the drawer's right edge. Full text is
 * always available via tooltip on hover.
 */
const MetricChip = ({ label, value }) => {
  const fullText =
    typeof value === "string" || typeof value === "number" ? String(value) : "";
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
          borderRadius: "4px",
          minWidth: 64,
          maxWidth: "100%",
          fontSize: 12,
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

/**
 * Inline tags row with add/edit/remove — ported from
 * `VoiceDetailDrawerV2/CallDetailsBar.jsx`. Tags persist on the chat
 * row's underlying trace record via the same `/tracer/trace/{id}/tags/`
 * PATCH that voice uses.
 */
const InlineTagsRow = ({ tags = [], entity }) => {
  const saveEntityTags = useSaveEntityTags();
  const [isAdding, setIsAdding] = useState(false);
  const queryClient = useQueryClient();

  // Until the refetch lands, the tags are the last acknowledged save.
  const latest = useLatestEntityTags(entity, tags);
  const normalized = useMemo(() => normalizeTags(latest.tags), [latest.tags]);
  // tag_target: null is a row the backend can show but not save from.
  const locked = entity?.tag_target === null;

  const { mutate, isPending } = useMutation({
    mutationFn: ({ tags: newTags, entity: savedEntity, base }) =>
      saveEntityTags({ entity: savedEntity, tags: newTags, base }),
    onSuccess: (result, { entity: savedEntity }) => {
      latest.confirm(result, savedEntity);
      return Promise.all(
        TAG_INVALIDATION_QUERY_KEYS.map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      );
    },
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      enqueueSnackbar(tagUpdateErrorMessage(error), { variant: "error" });
    },
    retry: false,
  });

  const persist = useCallback(
    (next) => mutate({ tags: next, entity, base: latest.base }),
    [mutate, entity, latest.base],
  );

  return (
    <Stack
      direction="row"
      sx={{ flexWrap: "wrap", gap: 0.5, alignItems: "center" }}
    >
      <Iconify
        icon="solar:tag-linear"
        width={14}
        sx={{ color: "text.disabled" }}
      />
      {normalized.map((tag, idx) => (
        <TagChip
          key={`${tag.name}-${idx}`}
          name={tag.name}
          color={tag.color}
          size="small"
          readOnly={locked || isPending || isSourceTag(entity, tag.name)}
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
      {!locked &&
        (isAdding ? (
          <Box
            sx={{ minWidth: 130 }}
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget))
                setIsAdding(false);
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
          <Button
            size="small"
            color="inherit"
            onClick={() => setIsAdding(true)}
            startIcon={<Iconify icon="solar:add-linear" width={12} />}
            sx={{ color: "text.secondary" }}
          >
            tag
          </Button>
        ))}
    </Stack>
  );
};
InlineTagsRow.propTypes = {
  tags: PropTypes.array,
  entity: PropTypes.object,
};

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

  // Tags persist on the trace record so we need a real `trace_id`
  // (CallExecution ids 404 against the tag endpoint). For the
  // simulate module we read tags from the data payload directly; for
  // observe (project) we fall back to fetching the canonical trace
  // detail so the chip row stays in sync.
  const traceId = data?.trace_id;
  const isObserve = data?.module === DRAWER_MODULE.OBSERVE;
  const { data: traceDetail } = useGetTraceDetail(
    isObserve && data?.project_id ? traceId : null,
    {
      projectId: data?.project_id,
    },
  );
  const tags =
    traceDetail?.trace?.tags ||
    traceDetail?.tags ||
    data?.tags ||
    data?.trace?.tags ||
    [];

  if (chips.length === 0 && !onAction && !traceId) return null;

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
          <VoiceActionsDropdown
            onAction={onAction}
            // Trace-gated actions (tags, dataset) are hidden when there's
            // no real trace_id so the menu doesn't render dead clicks.
            actions={
              data?.trace_id
                ? VOICE_ACTIONS
                : VOICE_ACTIONS.filter((a) => !TRACE_GATED_ACTION_IDS.has(a.id))
            }
          />
        </Stack>
      )}

      {chips.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{
            flexWrap: "wrap",
            minWidth: 0,
            width: "100%",
          }}
        >
          {chips.map((c) => (
            <MetricChip key={c.label} label={c.label} value={c.value} />
          ))}
        </Stack>
      )}

      {traceId && (
        <Box sx={{ mt: 0.75 }}>
          <InlineTagsRow
            tags={tags}
            entity={
              traceDetail?.trace ||
              (data?.tag_target ? data : data?.trace || data)
            }
          />
        </Box>
      )}
    </Box>
  );
};

ChatDetailsBar.propTypes = {
  data: PropTypes.object,
  onAction: PropTypes.func,
};

export default ChatDetailsBar;
