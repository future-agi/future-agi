// dt-5: the row view's sticky section bar,
// `Input · Expected · Generated · Evals · Metadata · Media (3)`. Only the
// sections the row has; each is a button (click, or Tab + Enter) that opens
// the section if folded and scrolls to it. `Media` jumps to the first media
// field (images, audio, documents, video, calls).
import React from "react";
import PropTypes from "prop-types";
import { ButtonBase, Stack, Typography } from "@mui/material";

export default function RowSectionBar({ sections, onJump }) {
  if (sections.length < 2) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      component="nav"
      aria-label="Row sections"
      data-row-section-bar=""
      sx={{
        // IT-R2 G5 P6: rendered inside the Find strip (no own row).
        gap: 0.25,
        minWidth: 0,
        overflowX: "auto",
        bgcolor: "background.paper",
        scrollbarWidth: "none",
      }}
    >
      {sections.map((s, i) => (
        <React.Fragment key={s.key}>
          {i > 0 ? (
            <Typography typography="s3" color="text.disabled" aria-hidden>
              ·
            </Typography>
          ) : null}
          <ButtonBase
            onClick={() => onJump(s)}
            data-row-section-jump={s.key}
            sx={{
              px: 0.75,
              height: 24,
              borderRadius: 0.5,
              typography: "s2",
              color: "text.secondary",
              whiteSpace: "nowrap",
              flexShrink: 0,
              "&:hover": { bgcolor: "action.hover", color: "text.primary" },
              "&.Mui-focusVisible": {
                outline: "2px solid",
                outlineColor: "primary.main",
              },
            }}
          >
            {s.count != null ? `${s.label} (${s.count})` : s.label}
          </ButtonBase>
        </React.Fragment>
      ))}
    </Stack>
  );
}

RowSectionBar.propTypes = {
  sections: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      count: PropTypes.number,
      selector: PropTypes.string.isRequired,
      section: PropTypes.string,
    }),
  ).isRequired,
  onJump: PropTypes.func.isRequired,
};
