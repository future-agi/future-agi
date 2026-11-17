import React, { memo } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";

export const ROW_HEADER_WIDTH = 260;

const tagSx = {
  px: 0.5,
  height: 18,
  lineHeight: "18px",
  borderRadius: "2px",
  typography: "s3",
  fontWeight: 500,
  whiteSpace: "nowrap",
  flexShrink: 0,
};

// `shrink`: the tag gives up width first (ellipsis, full text in the
// tooltip) so `Draft` and `Solved by n of 5` never clip (R2D-03).
const shrinkSx = {
  flexShrink: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
};

function Tag({ children, tone = "neutral", tooltip, shrink }) {
  const toneSx =
    tone === "amber"
      ? {
          bgcolor: (t) =>
            t.palette.mode === "dark"
              ? "rgba(245, 158, 11, 0.16)"
              : "rgba(245, 158, 11, 0.12)",
          color: "warning.dark",
        }
      : tone === "outlined"
        ? {
            border: "1px solid",
            borderColor: "divider",
            color: "text.secondary",
            lineHeight: "16px",
          }
        : { bgcolor: "action.hover", color: "text.secondary" };
  const tag = (
    <Box
      component="span"
      sx={{ ...tagSx, ...toneSx, ...(shrink ? shrinkSx : {}) }}
    >
      {children}
    </Box>
  );
  return tooltip ? (
    <CustomTooltip show size="small" placement="top" arrow title={tooltip}>
      {tag}
    </CustomTooltip>
  ) : (
    tag
  );
}

Tag.propTypes = {
  children: PropTypes.node,
  tone: PropTypes.string,
  tooltip: PropTypes.string,
  shrink: PropTypes.bool,
};

/** Draft evals: the row's Check evals are not reviewed yet. */
export const hasDraftEvals = (row) =>
  (row?.values?.checks ?? []).some?.((c) => c?.draft) ?? false;

/** `chat-1052` for a row imported from a session, else null. */
export const sessionOf = (row) =>
  row?.source === "session" || row?.group_values?.source === "session"
    ? row?.group_values?.session ?? row?.values?.source_session ?? null
    : null;

/**
 * Sticky row header (260 px): `T26` · `Task` · title · category chip ·
 * `From session chat-1052` · `Draft evals` · note glyph + count ·
 * `Solved by 4 of 5`.
 */
const MatrixRowHeader = ({
  row,
  dense,
  focused,
  onOpen,
  onCompare,
  showKind = true,
}) => {
  const notes = row.notes ?? [];
  const session = sessionOf(row);
  const category = row.group_values?.category;
  const notesTitle = notes
    .map((n) => `${n.author ?? "Reviewer"} · ${n.text}`)
    .join("\n");
  return (
    <Box
      role="rowheader"
      onClick={onOpen}
      sx={(t) => ({
        height: dense ? 40 : 56,
        px: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 0.25,
        cursor: onOpen ? "pointer" : "default",
        borderRight: "1px solid",
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        outline: focused ? `2px solid ${t.palette.primary.main}` : "none",
        outlineOffset: -2,
        "& .fa-row-compare": { opacity: focused ? 1 : 0 },
        "&:hover .fa-row-compare, & .fa-row-compare:focus-visible": {
          opacity: 1,
        },
      })}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {row.label}
        </Typography>
        {/* IT-R5 G6 P3: the kind chip only when row kinds are mixed. */}
        {showKind && row.kind === "task" && (
          <Tag tone="outlined">{VOCAB.taskBadge}</Tag>
        )}
        <CustomTooltip
          show={Boolean(row.title)}
          size="small"
          placement="top"
          arrow
          title={row.title ?? ""}
        >
          <Typography typography="s2" noWrap sx={{ minWidth: 0, flex: 1 }}>
            {row.title ?? ""}
          </Typography>
        </CustomTooltip>
        {notes.length > 0 && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={
              <Box component="span" sx={{ whiteSpace: "pre-line" }}>
                {notesTitle}
              </Box>
            }
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={0.25}
              aria-label={`${notes.length} ${notes.length === 1 ? "note" : "notes"}`}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            >
              <Iconify icon="mdi:comment-text-outline" width={14} />
              <Typography typography="s3">{notes.length}</Typography>
            </Stack>
          </CustomTooltip>
        )}
        {onCompare && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={`Open ${row.label} side by side: every run's steps in lanes`}
          >
            <IconButton
              className="fa-row-compare"
              size="small"
              aria-label={`Compare all runs on ${row.label}`}
              onClick={(e) => {
                e.stopPropagation();
                onCompare();
              }}
              sx={{ p: 0.25, flexShrink: 0, color: "primary.main" }}
            >
              <Iconify icon="mdi:compare-horizontal" width={16} />
            </IconButton>
          </CustomTooltip>
        )}
      </Stack>
      {!dense && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ minWidth: 0 }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ minWidth: 0, overflow: "hidden", flex: 1 }}
          >
            {category && (
              <Tag shrink tooltip={`Category: ${category}`}>
                {category}
              </Tag>
            )}
            {session && (
              <Tag
                shrink
                tooltip={`From session ${session}: imported from production`}
              >
                {session}
              </Tag>
            )}
            {hasDraftEvals(row) && (
              <Tag
                tone="amber"
                tooltip="Draft evals: this row's checks are not reviewed yet, so its numbers may move."
              >
                Draft
              </Tag>
            )}
          </Stack>
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{
              ml: "auto",
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {row.solvedText}
          </Typography>
        </Stack>
      )}
    </Box>
  );
};

MatrixRowHeader.propTypes = {
  row: PropTypes.object.isRequired,
  dense: PropTypes.bool,
  focused: PropTypes.bool,
  onOpen: PropTypes.func,
  onCompare: PropTypes.func,
  // IT-R5 G6 P3: false when every row is the same kind.
  showKind: PropTypes.bool,
};

export default memo(MatrixRowHeader);
