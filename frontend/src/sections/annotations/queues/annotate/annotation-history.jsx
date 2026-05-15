import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import {
  Avatar,
  Box,
  Chip,
  Collapse,
  Divider,
  IconButton,
  Popover,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useItemAnnotations } from "src/api/annotation-queues/annotation-queues";
import { fDateTime, fToNowStrict } from "src/utils/format-time";

const SOURCE_META = {
  human: { color: "primary.main", label: "Human" },
  automated: { color: "warning.main", label: "Automated" },
  imported: { color: "text.disabled", label: "Imported" },
};

const ROW_SX = {
  pl: 3.25,
  pr: 0.5,
  py: 0.25,
  minHeight: 22,
};

function annotatorInitial(name) {
  const trimmed = String(name || "?").trim();
  return trimmed.charAt(0).toUpperCase() || "?";
}

function isEdited(ann) {
  if (!ann?.updated_at || !ann?.created_at) return false;
  const created = new Date(ann.created_at).getTime();
  const updated = new Date(ann.updated_at).getTime();
  if (!Number.isFinite(created) || !Number.isFinite(updated)) return false;
  return updated - created > 2000;
}

function groupByAnnotator(annotations) {
  const groups = new Map();
  for (const ann of annotations) {
    const key = ann.annotator || ann.annotator_name || "Unknown";
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: ann.annotator_name || "Unknown",
        source: ann.score_source || "human",
        items: [],
        latest: ann.created_at || null,
      });
    }
    const g = groups.get(key);
    g.items.push(ann);
    if (
      ann.created_at &&
      (!g.latest || new Date(ann.created_at) > new Date(g.latest))
    ) {
      g.latest = ann.created_at;
    }
  }
  return Array.from(groups.values());
}

function SourceDot({ source }) {
  const meta = SOURCE_META[source] || SOURCE_META.human;
  return (
    <Tooltip title={meta.label} placement="top">
      <Box
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: meta.color,
          flexShrink: 0,
        }}
      />
    </Tooltip>
  );
}
SourceDot.propTypes = { source: PropTypes.string };

function EmptyValue() {
  return (
    <Typography variant="body2" color="text.disabled" sx={{ fontSize: 12 }}>
      —
    </Typography>
  );
}

function ScoreValue({ value, labelType, labelSettings, onShowRaw }) {
  const isEmpty =
    value === null ||
    value === undefined ||
    value === "" ||
    (Array.isArray(value) && value.length === 0);
  if (isEmpty) return <EmptyValue />;

  const settings = labelSettings || {};

  if (labelType === "categorical") {
    const selected = Array.isArray(value?.selected)
      ? value.selected
      : Array.isArray(value)
        ? value
        : null;
    if (selected) {
      const shown = selected.slice(0, 3);
      const extra = selected.length - shown.length;
      return (
        <Stack
          direction="row"
          spacing={0.5}
          alignItems="center"
          sx={{ flexWrap: "wrap", rowGap: 0.25 }}
        >
          {shown.map((s, i) => (
            <Chip
              key={i}
              label={String(s)}
              size="small"
              sx={{
                height: 18,
                fontSize: 11,
                "& .MuiChip-label": { px: 0.75 },
              }}
            />
          ))}
          {extra > 0 && (
            <Typography variant="caption" color="text.secondary">
              +{extra}
            </Typography>
          )}
        </Stack>
      );
    }
    const single =
      typeof value === "object" ? value.selected ?? value.value : value;
    return (
      <Chip
        label={String(single)}
        size="small"
        sx={{ height: 18, fontSize: 11, "& .MuiChip-label": { px: 0.75 } }}
      />
    );
  }

  if (labelType === "star") {
    const rating = Number(value?.rating ?? value);
    const max = Number(settings.no_of_stars) || 5;
    if (!Number.isFinite(rating)) return <EmptyValue />;
    const r = Math.max(0, Math.min(rating, max));
    const filled = Math.round(r);
    const stars = "★".repeat(filled) + "☆".repeat(Math.max(0, max - filled));
    return (
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Typography
          component="span"
          sx={{
            color: "warning.main",
            fontSize: 12,
            letterSpacing: -0.5,
            lineHeight: 1,
          }}
        >
          {stars}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {r}/{max}
        </Typography>
      </Stack>
    );
  }

  if (labelType === "thumbs_up_down") {
    const v = typeof value === "object" ? value?.value : value;
    const positive = v === "up";
    const negative = v === "down";
    if (!positive && !negative) return <EmptyValue />;
    return (
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Iconify
          icon={positive ? "octicon:thumbsup-24" : "octicon:thumbsdown-24"}
          width={13}
          sx={{
            color: positive ? "success.main" : "error.main",
            flexShrink: 0,
          }}
        />
        <Typography variant="body2" fontWeight={650} sx={{ fontSize: 12 }}>
          {positive ? "Yes" : "No"}
        </Typography>
      </Stack>
    );
  }

  if (labelType === "numeric") {
    const num = typeof value === "object" ? value?.value ?? null : value;
    if (num === null || num === undefined || num === "") return <EmptyValue />;
    return (
      <Typography
        variant="body2"
        sx={{
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontWeight: 650,
          fontSize: 12,
        }}
      >
        {String(num)}
      </Typography>
    );
  }

  if (labelType === "text" || labelType === "freeform") {
    const text = typeof value === "object" ? value?.text : value;
    if (text == null || text === "") return <EmptyValue />;
    const str = String(text);
    return (
      <Tooltip title={str} placement="top">
        <Typography
          variant="body2"
          sx={{
            display: "block",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontStyle: "italic",
            color: "text.secondary",
            fontSize: 12,
          }}
        >
          “{str}”
        </Typography>
      </Tooltip>
    );
  }

  if (typeof value !== "object") {
    return (
      <Typography variant="body2" fontWeight={650} sx={{ fontSize: 12 }}>
        {String(value)}
      </Typography>
    );
  }

  return (
    <Chip
      label="{…}"
      size="small"
      variant="outlined"
      onClick={(e) => {
        e.stopPropagation();
        onShowRaw?.(e.currentTarget, value);
      }}
      sx={{
        height: 18,
        fontSize: 11,
        fontFamily: "ui-monospace, monospace",
        cursor: "pointer",
        "& .MuiChip-label": { px: 0.75 },
      }}
    />
  );
}
ScoreValue.propTypes = {
  value: PropTypes.any,
  labelType: PropTypes.string,
  labelSettings: PropTypes.object,
  onShowRaw: PropTypes.func,
};

AnnotationHistory.propTypes = {
  queueId: PropTypes.string.isRequired,
  itemId: PropTypes.string,
};

export default function AnnotationHistory({ queueId, itemId }) {
  const [open, setOpen] = useState(false);
  const [rawAnchor, setRawAnchor] = useState(null);
  const [rawValue, setRawValue] = useState(null);

  const { data: annotations = [] } = useItemAnnotations(queueId, itemId, {
    enabled: !!queueId && !!itemId,
  });

  const groups = useMemo(() => groupByAnnotator(annotations), [annotations]);

  if (!itemId) return null;

  const handleShowRaw = (target, value) => {
    setRawAnchor(target);
    setRawValue(value);
  };
  const closeRaw = () => {
    setRawAnchor(null);
    setRawValue(null);
  };

  return (
    <Box sx={{ mt: 2 }}>
      <Divider sx={{ mb: 1 }} />
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        onClick={() => setOpen(!open)}
        sx={{ cursor: "pointer" }}
      >
        <Typography variant="caption" fontWeight={600} color="text.secondary">
          ANNOTATION HISTORY ({annotations.length})
        </Typography>
        <IconButton size="small">
          <Iconify
            icon={open ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
            width={16}
          />
        </IconButton>
      </Stack>
      <Collapse in={open}>
        {groups.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            No annotations yet
          </Typography>
        ) : (
          <Stack spacing={1.25} sx={{ mt: 1 }}>
            {groups.map((group) => (
              <Box key={group.key}>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mb: 0.5 }}
                >
                  <Avatar
                    sx={{
                      width: 18,
                      height: 18,
                      fontSize: 10,
                      bgcolor:
                        group.source === "automated"
                          ? "warning.lighter"
                          : "primary.lighter",
                      color:
                        group.source === "automated"
                          ? "warning.darker"
                          : "primary.darker",
                      fontWeight: 700,
                    }}
                  >
                    {group.source === "automated" ? (
                      <Iconify icon="mdi:robot-outline" width={11} />
                    ) : (
                      annotatorInitial(group.name)
                    )}
                  </Avatar>
                  <Typography
                    variant="caption"
                    fontWeight={700}
                    sx={{ fontSize: 12 }}
                  >
                    {group.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    · {group.items.length} score
                    {group.items.length === 1 ? "" : "s"}
                  </Typography>
                  <Box sx={{ flex: 1 }} />
                  {group.latest && (
                    <Tooltip title={fDateTime(group.latest)} placement="top">
                      <Typography variant="caption" color="text.secondary">
                        {fToNowStrict(group.latest)}
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
                {group.items.map((ann) => (
                  <Stack
                    key={ann.id}
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={ROW_SX}
                  >
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      title={ann.label_name}
                      sx={{
                        minWidth: 90,
                        flexShrink: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontSize: 11.5,
                      }}
                    >
                      {ann.label_name}
                    </Typography>
                    <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      <ScoreValue
                        value={ann.value}
                        labelType={ann.label_type}
                        labelSettings={ann.label_settings}
                        onShowRaw={handleShowRaw}
                      />
                    </Box>
                    {isEdited(ann) && (
                      <Tooltip
                        placement="top"
                        title={
                          <Box>
                            <Box>Edited {fToNowStrict(ann.updated_at)}</Box>
                            <Box sx={{ opacity: 0.75 }}>
                              First submitted {fToNowStrict(ann.created_at)}
                            </Box>
                          </Box>
                        }
                      >
                        <Typography
                          component="span"
                          variant="caption"
                          sx={{
                            fontSize: 10.5,
                            fontStyle: "italic",
                            color: "text.disabled",
                            lineHeight: 1,
                            flexShrink: 0,
                          }}
                        >
                          edited
                        </Typography>
                      </Tooltip>
                    )}
                    {ann.notes && (
                      <Tooltip title={ann.notes} placement="top">
                        <Box sx={{ display: "flex", alignItems: "center" }}>
                          <Iconify
                            icon="eva:message-square-outline"
                            width={12}
                            sx={{ color: "text.disabled" }}
                          />
                        </Box>
                      </Tooltip>
                    )}
                    <SourceDot source={ann.score_source} />
                  </Stack>
                ))}
              </Box>
            ))}
          </Stack>
        )}
      </Collapse>

      <Popover
        open={Boolean(rawAnchor)}
        anchorEl={rawAnchor}
        onClose={closeRaw}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              maxWidth: 360,
              maxHeight: 280,
              p: 1.25,
              borderRadius: 0.75,
            },
          },
        }}
      >
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mb: 0.5, fontWeight: 700 }}
        >
          Raw value
        </Typography>
        <Box
          component="pre"
          sx={{
            m: 0,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "text.primary",
          }}
        >
          {rawValue ? JSON.stringify(rawValue, null, 2) : ""}
        </Box>
      </Popover>
    </Box>
  );
}
