import React, { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import TextArrayJsonCellRenderer from "./TextArrayJsonCellRenderer";
import { commonPropTypes } from "./cellRendererHelper";
import {
  conversationSummary,
  dotText,
  parseConversation,
  roleLabel,
  turnDots,
} from "src/sections/develop-detail/TaskSuite/itemModel";

const DOT_COLOR = { pass: "green.600", fail: "red.600", none: "text.disabled" };

const clip = (s, n) =>
  s && s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s || "";

/**
 * `conversation` cells (R2-SPEC §7.3.6): one dot per turn coloured by its
 * Helpfulness (green pass, red fail, grey not scored; a tooltip per dot)
 * and `6 turns · 3 tool calls · ends: "…"`. Hover the text: the first and
 * last messages. Values that are not conversations fall back to the JSON
 * cell.
 *
 * Props (frozen): commonPropTypes + `isHover`, `cellData`.
 */
const ConversationCellRenderer = (props) => {
  const { value } = props;
  const conv = useMemo(() => parseConversation(value), [value]);
  if (!conv) return <TextArrayJsonCellRenderer {...props} />;
  const dots = turnDots(conv);
  const first = conv.turns[0];
  const last = conv.turns[conv.turns.length - 1];
  const hover = (
    <Stack gap={0.75} sx={{ maxWidth: 360, py: 0.25 }}>
      {[first, last !== first ? last : null].filter(Boolean).map((t) => (
        <Box key={t.n}>
          <Typography typography="s3" color="text.disabled">
            Turn {t.n} · {roleLabel(t.role)}
          </Typography>
          <Typography typography="s2" sx={{ whiteSpace: "normal" }}>
            {clip(t.content, 220) || "No text"}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        height: "100%",
        px: 1,
        minWidth: 0,
      }}
      data-testid="conversation-cell"
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.375}
        sx={{ flexShrink: 0 }}
        role="img"
        aria-label={`Turns ${dotText(dots)}`}
      >
        {dots.slice(0, 24).map((d) => (
          <CustomTooltip
            key={d.n}
            show
            size="small"
            placement="top"
            arrow
            title={d.tooltip}
          >
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                flexShrink: 0,
                bgcolor:
                  d.verdict === "none" ? "transparent" : DOT_COLOR[d.verdict],
                border: "1.5px solid",
                borderColor: DOT_COLOR[d.verdict],
              }}
            />
          </CustomTooltip>
        ))}
        {dots.length > 24 && (
          <Typography typography="s3" color="text.disabled">
            +{dots.length - 24}
          </Typography>
        )}
      </Stack>
      <CustomTooltip show size="small" placement="bottom-start" title={hover}>
        <Typography
          typography="s2"
          color="text.primary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {conversationSummary(conv)}
        </Typography>
      </CustomTooltip>
    </Box>
  );
};

ConversationCellRenderer.propTypes = commonPropTypes;

export default ConversationCellRenderer;
