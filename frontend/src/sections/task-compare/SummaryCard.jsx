import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";

const TONE_ICON = {
  pass: ["mdi:check-circle", "success.main"],
  fail: ["mdi:close-circle", "error.main"],
  mixed: ["mdi:circle-half-full", "warning.main"],
  none: ["mdi:minus-circle-outline", "text.secondary"],
};
/**
 * The 10-second answer (IT-R2 G4 P8): ONE sentence above the steps — who
 * took how many steps and how it ended, the why on the run it is about
 * (`storyGrid.summarySentence`), two lines at most; `Jump to step ↵` and
 * `Rewrite with Falcon` at its end. Full text in the tooltip.
 */
export default function SummaryCard({
  summary,
  sentence,
  why,
  lanes,
  onJump,
  onRewrite,
  lines = 1,
  differently = null,
  trials = null,
}) {
  const text = sentence || [summary?.text, why].filter(Boolean).join(" ");
  // One line by default (the steps keep the room); a click shows it all.
  const [open, setOpen] = useState(false);
  const allPass =
    lanes.length > 0 &&
    lanes.every(
      (l) => l.verdict?.scored && l.verdict.pass === l.verdict.scored,
    );
  const [icon, color] = allPass
    ? TONE_ICON.pass
    : ["mdi:flag-checkered", "text.secondary"];
  return (
    <Box
      component="section"
      aria-label="Summary"
      data-testid="tc-summary"
      sx={{
        px: 2,
        py: 0.5,
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ minWidth: 0 }}
      >
        <Iconify icon={icon} width={16} sx={{ color, flexShrink: 0 }} />
        <CustomTooltip describeChild show size="small" title={text}>
          <Typography
            typography="s1"
            data-testid="tc-summary-sentence"
            role="button"
            tabIndex={0}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            onKeyDown={(e) =>
              (e.key === "Enter" || e.key === " ") &&
              (e.preventDefault(), setOpen((v) => !v))
            }
            sx={{
              minWidth: 0,
              flex: 1,
              cursor: "pointer",
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: open ? 6 : lines,
              WebkitBoxOrient: "vertical",
              lineHeight: "20px",
              "&:focus-visible": { outline: "2px solid", outlineOffset: 1 },
            }}
          >
            {text}
          </Typography>
        </CustomTooltip>
        {onJump ? (
          <KeyHintTooltip
            title="Jump to the first wrong step and open it"
            action="jumpFailedCheck"
          >
            <Button
              size="small"
              onClick={onJump}
              sx={{
                typography: "s2",
                px: 0.75,
                py: 0,
                height: 24,
                minWidth: 0,
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              Jump to step ↵
            </Button>
          </KeyHintTooltip>
        ) : null}
        {onRewrite ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title="Rewrite with Falcon: plain words for a reviewer. The mechanical line stays the source of truth."
          >
            <IconButton
              size="small"
              onClick={onRewrite}
              aria-label="Rewrite with Falcon"
              sx={{ flexShrink: 0, color: "text.secondary", p: 0.25 }}
            >
              <Iconify icon="mdi:creation" width={16} />
            </IconButton>
          </CustomTooltip>
        ) : null}
      </Stack>
      {differently || trials ? (
        // IT-R4 G4 P4: what the other run did differently (step diff) and
        // which tries are shown, and why.
        <Stack direction="row" alignItems="center" gap={1} sx={{ pl: 3 }}>
          <CustomTooltip
            describeChild
            show
            size="small"
            title={differently ?? ""}
          >
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="tc-differently"
              sx={{
                flex: 1,
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: open ? "normal" : "nowrap",
                lineHeight: "18px",
              }}
            >
              {differently}
            </Typography>
          </CustomTooltip>
          {trials ? (
            <CustomTooltip
              describeChild
              show
              size="small"
              title={`${trials.caption} To change, click a dot in a lane header (or focus it and use the arrow keys; [ and ] step through).`}
            >
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="tc-trial-caption"
                tabIndex={0}
                sx={{
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                  cursor: "default",
                  "&:focus-visible": { outline: "2px solid", outlineOffset: 1 },
                }}
              >
                {trials.short}
              </Typography>
            </CustomTooltip>
          ) : null}
        </Stack>
      ) : null}
    </Box>
  );
}

SummaryCard.propTypes = {
  summary: PropTypes.shape({ text: PropTypes.string, parts: PropTypes.array })
    .isRequired,
  sentence: PropTypes.string,
  why: PropTypes.string,
  lanes: PropTypes.array.isRequired,
  names: PropTypes.instanceOf(Map).isRequired,
  baselineId: PropTypes.string,
  onJump: PropTypes.func,
  onRewrite: PropTypes.func,
  lines: PropTypes.number,
  differently: PropTypes.string,
  trials: PropTypes.shape({
    caption: PropTypes.string,
    short: PropTypes.string,
  }),
};
