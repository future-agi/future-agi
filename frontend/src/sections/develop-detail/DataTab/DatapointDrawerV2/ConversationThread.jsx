import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Collapse,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { copyToClipboard } from "src/utils/utils";
import { fDateTime } from "src/utils/format-time";
import { roleLabel } from "src/sections/develop-detail/TaskSuite/itemModel";
import { normalizeToolCall, toolCallText, toolOutcome } from "./toolCallModel";

const TONE = { ok: "green.600", error: "red.600" };

/** A recorded tool call: collapsed chip `lookup_order(order_id="8830") → 404`. */
const ToolCallChip = ({ call, messages }) => {
  const [open, setOpen] = useState(false);
  const normalized = normalizeToolCall(call, { messages });
  const out = toolOutcome(normalized);
  const text = toolCallText(normalized);
  const arrow = text.lastIndexOf(" → ");
  return (
    <Box sx={{ minWidth: 0 }}>
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="tool-call-chip"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          maxWidth: "100%",
          height: 24,
          px: 0.75,
          borderRadius: 0.75,
          border: "1px solid",
          borderColor: (t) =>
            alpha(
              out.tone === "error" ? t.palette.red[600] : t.palette.green[600],
              0.4,
            ),
          bgcolor: "background.paper",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          typography: "s3",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Iconify
          icon="mdi:wrench-outline"
          width={12}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "text.primary",
          }}
        >
          {text.slice(0, arrow)}
        </Box>
        <Box
          component="span"
          sx={{ color: TONE[out.tone], fontWeight: 600, flexShrink: 0 }}
        >
          → {out.label}
        </Box>
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Stack
          gap={1}
          sx={{
            mt: 0.75,
            p: 1,
            borderRadius: 0.75,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Typography typography="s3" color="text.secondary">
            Arguments
          </Typography>
          <ValueViewer
            value={normalized.args ?? {}}
            dataType="json"
            emptyLabel="none"
          />
          <Typography typography="s3" color="text.secondary">
            Recorded result{out.message ? ` · ${out.message}` : ""}
          </Typography>
          <ValueViewer
            value={normalized.result ?? null}
            dataType="json"
            emptyLabel="none"
          />
        </Stack>
      </Collapse>
    </Box>
  );
};
ToolCallChip.propTypes = {
  call: PropTypes.object.isRequired,
  messages: PropTypes.any,
};

/** Image (lightbox on click) or audio (player, duration, transcript toggle). */
const Attachment = ({ item, transcript }) => {
  const [zoom, setZoom] = useState(false);
  const [showText, setShowText] = useState(false);
  const [duration, setDuration] = useState(null);
  const kind =
    item.kind ??
    (String(item.mime ?? "").startsWith("audio") ? "audio" : "image");
  if (kind === "audio") {
    const lines = item.transcript ?? transcript;
    return (
      <Stack gap={0.5} sx={{ minWidth: 0 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Box
            component="audio"
            controls
            src={item.url}
            onLoadedMetadata={(e) => {
              const d = e.currentTarget.duration;
              if (Number.isFinite(d)) setDuration(d);
            }}
            sx={{ height: 32, maxWidth: 280 }}
          />
          <Typography typography="s3" color="text.secondary">
            {duration != null ? `${duration.toFixed(1)} s` : "Audio"}
          </Typography>
          {lines?.length ? (
            <ButtonBase
              onClick={() => setShowText((v) => !v)}
              sx={{ typography: "s3", color: "primary.main", px: 0.5 }}
            >
              {showText ? "Hide transcript" : "Transcript"}
            </ButtonBase>
          ) : null}
        </Stack>
        <Collapse in={showText} unmountOnExit>
          <Stack gap={0.25} sx={{ pl: 0.5 }}>
            {(Array.isArray(lines) ? lines : [lines]).map((l, i) => (
              <Typography key={i} typography="s3" color="text.secondary">
                {typeof l === "string"
                  ? l
                  : `${l.speaker ?? "Speaker"}: ${l.text ?? ""}`}
              </Typography>
            ))}
          </Stack>
        </Collapse>
      </Stack>
    );
  }
  return (
    <>
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`${item.name ?? "Image"} · click to enlarge`}
      >
        <ButtonBase
          onClick={() => setZoom(true)}
          data-testid="attachment-image"
          sx={{
            borderRadius: 1,
            overflow: "hidden",
            border: "1px solid",
            borderColor: "divider",
            width: 160,
            height: 120,
            bgcolor: "background.neutral",
          }}
        >
          <Box
            component="img"
            src={item.url}
            alt={item.name ?? "attachment"}
            sx={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        </ButtonBase>
      </CustomTooltip>
      <Dialog open={zoom} onClose={() => setZoom(false)} maxWidth="md">
        <Stack sx={{ p: 1.5 }} gap={1}>
          <Stack direction="row" alignItems="center">
            <Typography typography="s1" fontWeight={600} sx={{ flex: 1 }}>
              {item.name ?? "Image"}
            </Typography>
            <IconButton
              size="small"
              aria-label="Close"
              onClick={() => setZoom(false)}
            >
              <Iconify icon="mdi:close" width={18} />
            </IconButton>
          </Stack>
          <Box
            component="img"
            src={item.url}
            alt={item.name ?? "attachment"}
            sx={{ maxWidth: "80vw", maxHeight: "75vh", borderRadius: 1 }}
          />
        </Stack>
      </Dialog>
    </>
  );
};
Attachment.propTypes = {
  item: PropTypes.object.isRequired,
  transcript: PropTypes.any,
};

const EvalBadge = ({ verdict }) => {
  if (!verdict) return null;
  const pass = verdict === "Pass";
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.25,
        height: 18,
        px: 0.5,
        borderRadius: 0.5,
        typography: "s3",
        fontWeight: 600,
        bgcolor: (t) =>
          alpha(pass ? t.palette.green[600] : t.palette.red[600], 0.12),
        color: pass ? "green.600" : "red.600",
      }}
    >
      <Iconify icon={pass ? "mdi:check" : "mdi:close"} width={12} />
      Helpfulness
    </Box>
  );
};
EvalBadge.propTypes = { verdict: PropTypes.string };

/**
 * The conversation as bubbles (§7.3.6): customer left, agent right, turn
 * numbers, per-turn Helpfulness, collapsed tool-call chips with the
 * recorded results, attachments inline, a copy button on hover. With
 * `replayTurn`, that turn is highlighted as the first failing turn.
 */
const ConversationThread = ({
  conv,
  replayTurn,
  flashTurn,
  turnRef,
  compact = false,
  failNote = null,
  toolSource,
}) => {
  const turns = conv.turns.filter(
    (t) => t.role !== "tool" && t.role !== "system",
  );
  // Tool messages show inside their call's chip, so the visible turns are
  // numbered 1…N with no gaps (the same N as countTurns).
  const shownNo = new Map(turns.map((t, i) => [t.n, i + 1]));
  const failText =
    failNote && shownNo.get(failNote.turn) !== failNote.turn
      ? String(failNote.text ?? "").replace(
          new RegExp(`^Turn ${failNote.turn}\\b`),
          `Turn ${shownNo.get(failNote.turn) ?? failNote.turn}`,
        )
      : failNote?.text;
  return (
    <Stack gap={compact ? 1 : 1.5} data-testid="conversation-thread">
      {turns.map((t) => {
        const agent = t.role === "agent";
        const marked = t.n === replayTurn;
        return (
          <Stack
            key={t.n}
            ref={(el) => turnRef?.(t.n, el)}
            id={`turn-${t.n}`}
            data-testid={`turn-${t.n}`}
            alignItems={agent ? "flex-end" : "flex-start"}
            gap={0.5}
            sx={{ scrollMarginTop: 96 }}
          >
            {/* IT-R3 G5 P2: the failing turn names its eval and reason
                (`Turn 2 · Resolution failed: …`). */}
            {failNote && failNote.turn === t.n ? (
              <Typography
                typography="s3"
                fontWeight={600}
                color="red.600"
                data-testid="failing-turn-label"
                sx={{ maxWidth: compact ? "92%" : "84%", textAlign: "right" }}
              >
                {failText}
              </Typography>
            ) : (
              marked && (
                <Typography
                  typography="s3"
                  fontWeight={600}
                  color="red.600"
                  data-testid="failing-turn-label"
                >
                  First failing turn
                </Typography>
              )
            )}
            <Box
              sx={{
                position: "relative",
                maxWidth: compact ? "92%" : "84%",
                minWidth: 120,
                px: 1.5,
                py: 1,
                borderRadius: 1.5,
                border: "1px solid",
                borderColor:
                  marked || failNote?.turn === t.n ? "red.600" : "divider",
                bgcolor: (th) =>
                  marked
                    ? alpha(th.palette.red[600], 0.06)
                    : agent
                      ? alpha(th.palette.primary.main, 0.06)
                      : th.palette.background.neutral,
                boxShadow: (th) =>
                  flashTurn === t.n
                    ? `0 0 0 3px ${alpha(th.palette.primary.main, 0.35)}`
                    : "none",
                transition: "box-shadow 300ms",
                "&:hover .bubble-tools": { opacity: 1 },
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                sx={{ mb: 0.25, minHeight: 20 }}
              >
                <Typography
                  typography="s3"
                  color="text.secondary"
                  fontWeight={600}
                >
                  {shownNo.get(t.n) ?? t.n} · {roleLabel(t.role)}
                </Typography>
                <EvalBadge verdict={t.helpfulness} />
                <Box sx={{ flex: 1 }} />
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={0.25}
                  className="bubble-tools"
                  sx={{ opacity: 0, transition: "opacity 120ms" }}
                >
                  {t.at && (
                    <Typography typography="s3" color="text.disabled">
                      {fDateTime(t.at, "MMM d, HH:mm:ss")}
                    </Typography>
                  )}
                  <CustomTooltip
                    show
                    size="small"
                    placement="top"
                    arrow
                    title="Copy message"
                  >
                    <IconButton
                      size="small"
                      aria-label="Copy message"
                      sx={{ p: 0.25 }}
                      onClick={() => {
                        copyToClipboard(t.content);
                        enqueueSnackbar("Message copied", {
                          variant: "success",
                        });
                      }}
                    >
                      <Iconify icon="mdi:content-copy" width={12} />
                    </IconButton>
                  </CustomTooltip>
                </Stack>
              </Stack>
              <Typography
                typography="s2"
                sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
              >
                {t.content || (
                  <Box
                    component="span"
                    sx={{ color: "text.disabled", fontStyle: "italic" }}
                  >
                    No text
                  </Box>
                )}
              </Typography>
              {t.attachments.length > 0 && (
                <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}>
                  {t.attachments.map((a, i) => (
                    <Attachment
                      key={a.name ?? i}
                      item={a}
                      transcript={conv.transcript}
                    />
                  ))}
                </Stack>
              )}
              {t.toolCalls.length > 0 && (
                <Stack gap={0.5} sx={{ mt: 1 }}>
                  {t.toolCalls.map((c, i) => (
                    <ToolCallChip
                      key={`${c.name ?? c.function?.name ?? "tool"}-${i}`}
                      call={c}
                      messages={toolSource}
                    />
                  ))}
                </Stack>
              )}
            </Box>
          </Stack>
        );
      })}
      {conv.audioUrl && (
        <Attachment
          item={{ kind: "audio", url: conv.audioUrl, name: "Call recording" }}
          transcript={conv.transcript}
        />
      )}
    </Stack>
  );
};

ConversationThread.propTypes = {
  conv: PropTypes.object.isRequired,
  replayTurn: PropTypes.number,
  flashTurn: PropTypes.number,
  turnRef: PropTypes.func,
  compact: PropTypes.bool,
  failNote: PropTypes.shape({ turn: PropTypes.number, text: PropTypes.string }),
  toolSource: PropTypes.any,
};

export default ConversationThread;
