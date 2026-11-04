import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { REVIEW_COPY as COPY } from "../workspace.constants";
import { diffLines, sourceLine, sourceLink, undoBlock } from "./reviewView";

const DIFF_LINES_SHOWN = 12;
// A long file name gives way before the row's title does.
const LINK_MAX_WIDTH = 200;

const SIGN_COLOR = {
  "+": "status.success.fg",
  "−": "status.danger.fg",
  "~": "text.secondary",
};

const clock = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

const sourceText = (source) => {
  if (!source) return COPY.noSource;
  const line = sourceLine(source);
  const from = {
    chat: COPY.fromChat,
    file: COPY.fromFile(line.file),
    hand: COPY.byHand,
    update: COPY.fromUpdate,
  }[line.kind];
  return [from, line.where, clock(line.at)].filter(Boolean).join(" · ");
};

const linkText = (link) => {
  if (link.kind === "chat") return COPY.openInChat;
  return link.kind === "file" ? COPY.openFile(link.file) : COPY.open;
};

function DiffLine({ line }) {
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      <Typography
        sx={{
          typography: "code_sm",
          fontWeight: 600,
          color: SIGN_COLOR[line.sign],
          width: 10,
          flexShrink: 0,
        }}
      >
        {line.sign}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 92,
          flexShrink: 0,
        }}
      >
        {line.field}
      </Typography>
      <Box sx={{ minWidth: 0 }}>
        {line.before && (
          <Typography
            sx={{
              typography: "code_sm",
              color: "text.tertiary",
              textDecoration: "line-through",
              wordBreak: "break-word",
            }}
          >
            {line.before}
          </Typography>
        )}
        <Typography
          sx={{
            typography: "code_sm",
            color: "text.primary",
            wordBreak: "break-word",
          }}
        >
          {line.text}
        </Typography>
      </Box>
    </Stack>
  );
}
DiffLine.propTypes = {
  line: PropTypes.shape({
    sign: PropTypes.string,
    field: PropTypes.string,
    before: PropTypes.string,
    text: PropTypes.string,
  }).isRequired,
};

/** One unsaved change: what it is, where it came from, its diff and its Undo. */
export default function ChangeRow({
  change,
  changes,
  canEdit,
  onUndo,
  onOpen,
}) {
  const [expanded, setExpanded] = useState(false);
  const lines = diffLines(change);
  const source = change.sources?.[0];
  const block = undoBlock(change, changes);
  const link = sourceLink(source);
  return (
    <Box
      data-testid={`change-${change.id}`}
      sx={{ "& + &": { borderTop: "1px solid", borderColor: "divider" } }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, minHeight: 44, py: 0.5 }}
      >
        <IconButton
          size="small"
          aria-label={expanded ? COPY.hideChange : COPY.showChange}
          aria-expanded={expanded}
          disabled={!lines.length}
          onClick={() => setExpanded((open) => !open)}
          sx={{ flexShrink: 0, ml: -0.5 }}
        >
          <Iconify
            icon={
              expanded
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={14}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
        <Box flex={1} minWidth={0}>
          <OverflowTooltip title={change.summary}>
            <Typography noWrap sx={{ typography: "s1" }}>
              {change.summary}
            </Typography>
          </OverflowTooltip>
          <OverflowTooltip title={sourceText(source)}>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {sourceText(source)}
            </Typography>
          </OverflowTooltip>
        </Box>
        <Button
          size="small"
          color="inherit"
          onClick={() => onOpen(link)}
          sx={{
            flexShrink: 0,
            maxWidth: LINK_MAX_WIDTH,
            color: "text.secondary",
            whiteSpace: "nowrap",
          }}
        >
          <OverflowTooltip title={linkText(link)}>
            <Box
              component="span"
              sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
            >
              {linkText(link)}
            </Box>
          </OverflowTooltip>
        </Button>
        {canEdit && (
          <CustomTooltip
            show={Boolean(block)}
            size="small"
            arrow
            title={block ? COPY.undoBlocked(block.by) : ""}
          >
            <span>
              {/* Held, not disabled: a disabled button can't take focus, and
                  the reason it's held would be out of reach by keyboard. */}
              <Button
                size="small"
                color="inherit"
                data-review-focus
                className={block ? "Mui-disabled" : undefined}
                aria-disabled={block ? true : undefined}
                aria-label={
                  block
                    ? `${COPY.undo}. ${COPY.undoBlocked(block.by)}`
                    : undefined
                }
                startIcon={
                  <Iconify icon="solar:undo-left-round-linear" width={14} />
                }
                onClick={block ? undefined : onUndo}
              >
                {COPY.undo}
              </Button>
            </span>
          </CustomTooltip>
        )}
      </Stack>
      <Collapse in={expanded} unmountOnExit>
        <Stack spacing={0.25} sx={{ px: 1.5, pb: 1.25, pl: 5.5 }}>
          {lines.slice(0, DIFF_LINES_SHOWN).map((line, index) => (
            // eslint-disable-next-line react/no-array-index-key
            <DiffLine key={index} line={line} />
          ))}
          {lines.length > DIFF_LINES_SHOWN && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.andMore(lines.length - DIFF_LINES_SHOWN)}
            </Typography>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}

ChangeRow.propTypes = {
  change: PropTypes.object.isRequired,
  // Every change in the review, to see which kept one needs this.
  changes: PropTypes.arrayOf(PropTypes.object).isRequired,
  canEdit: PropTypes.bool,
  onUndo: PropTypes.func,
  // (source link): go to where the change came from.
  onOpen: PropTypes.func,
};
