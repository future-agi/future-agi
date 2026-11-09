import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Popover,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import KeyCap from "src/sections/common/ux/KeyCap";

const Sep = () => (
  <Box
    component="span"
    sx={{ width: "1px", height: 18, bgcolor: "divider", flexShrink: 0 }}
  />
);

/**
 * The review bar: one slim line pinned under the runs —
 * `Which did better? [1] … [2] Tie [3] … [4] Both bad · Mark viewed (V) ·
 * Notes` and the key hints. Notes open a small popover; answers and checks
 * are tabs of the Inspect panel above the bar.
 */
export default function BottomDock({
  verdictBar,
  viewed,
  onViewed,
  notesCount,
  notesOpen,
  onNotes,
  notes,
}) {
  // State, not a ref: a pasted `dock=notes` link opens the popover once the
  // button mounts.
  const [notesBtn, setNotesBtn] = useState(null);
  const roomy = useMediaQuery("(min-width:1600px)");
  return (
    <Box
      component="section"
      aria-label="Review"
      data-testid="tc-review-bar"
      sx={{
        flexShrink: 0,
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 -2px 8px rgba(0,0,0,0.04)",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, height: 40, flexShrink: 0, minWidth: 0 }}
      >
        <Box sx={{ flexShrink: 1, minWidth: 0, display: "flex" }}>
          {verdictBar}
        </Box>
        <Sep />
        <KeyHintTooltip
          title={
            viewed
              ? "Viewed · press again to unmark"
              : "Mark this task as viewed"
          }
          action="markViewed"
        >
          <Button
            size="small"
            variant={viewed ? "contained" : "text"}
            color={viewed ? "success" : "inherit"}
            onClick={onViewed}
            startIcon={
              <Iconify
                icon={viewed ? "mdi:check-circle" : "mdi:check-circle-outline"}
                width={16}
              />
            }
            sx={{ flexShrink: 0, whiteSpace: "nowrap", px: 1 }}
            aria-pressed={viewed}
          >
            {viewed ? "Viewed" : "Mark viewed"}
            <KeyCap sx={{ ml: 0.75 }}>V</KeyCap>
          </Button>
        </KeyHintTooltip>
        <Sep />
        <KeyHintTooltip title="Notes on this task" action="addNote">
          <Button
            ref={setNotesBtn}
            size="small"
            color="inherit"
            onClick={() => onNotes(!notesOpen)}
            aria-haspopup="dialog"
            aria-expanded={notesOpen}
            startIcon={<Iconify icon="mdi:comment-text-outline" width={16} />}
            sx={{ flexShrink: 0, whiteSpace: "nowrap", px: 1 }}
          >
            {`Notes${notesCount ? ` (${notesCount})` : ""}`}
          </Button>
        </KeyHintTooltip>
        <Box sx={{ flex: 1 }} />
        {roomy ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            <KeyCap>J</KeyCap>
            <KeyCap>K</KeyCap> next task
          </Typography>
        ) : null}
        <CustomTooltip
          describeChild
          show
          size="small"
          title="All keyboard shortcuts"
        >
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            <KeyCap>?</KeyCap>
            {roomy ? " keys" : ""}
          </Typography>
        </CustomTooltip>
      </Stack>
      <Popover
        open={Boolean(notesOpen && notesBtn)}
        anchorEl={notesBtn}
        onClose={() => onNotes(false)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: { sx: { width: 420, maxHeight: 440, overflow: "auto" } },
        }}
      >
        {notes}
      </Popover>
    </Box>
  );
}

BottomDock.propTypes = {
  verdictBar: PropTypes.node,
  viewed: PropTypes.bool,
  onViewed: PropTypes.func.isRequired,
  notesCount: PropTypes.number,
  notesOpen: PropTypes.bool,
  onNotes: PropTypes.func.isRequired,
  notes: PropTypes.node,
};
