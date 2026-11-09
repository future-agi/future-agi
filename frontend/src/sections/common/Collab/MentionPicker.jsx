import React from "react";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  ButtonBase,
  Paper,
  Popper,
  Stack,
  Typography,
} from "@mui/material";

const initial = (name) =>
  String(name || "?")
    .trim()
    .charAt(0)
    .toUpperCase() || "?";

/**
 * W4-1: the @ member list under the composer. Members come from the one
 * org users endpoint; people who can't view the dataset are listed last
 * with "No access" (picking them still works; the composer explains).
 */
const MentionPicker = ({
  anchorEl,
  open,
  candidates,
  active,
  onPick,
  loading,
}) => (
  <Popper
    open={Boolean(open && anchorEl)}
    anchorEl={anchorEl}
    placement="top-start"
    sx={{ zIndex: (t) => t.zIndex.modal + 2 }}
  >
    <Paper
      elevation={6}
      data-testid="mention-picker"
      role="listbox"
      aria-label="Mention someone"
      sx={{
        minWidth: 260,
        maxWidth: 340,
        maxHeight: 260,
        overflowY: "auto",
        py: 0.5,
      }}
    >
      {candidates.length === 0 ? (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 1.5, py: 1 }}
        >
          {loading ? "Looking up people…" : "No one in this workspace matches."}
        </Typography>
      ) : (
        candidates.map((m, i) => (
          <ButtonBase
            key={m.id}
            role="option"
            aria-selected={i === active}
            data-testid="mention-option"
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(m);
            }}
            sx={{
              width: 1,
              px: 1.25,
              py: 0.625,
              justifyContent: "flex-start",
              bgcolor: i === active ? "action.selected" : "transparent",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ minWidth: 0, width: 1 }}
            >
              <Avatar
                sx={{ width: 22, height: 22, fontSize: 11, fontWeight: 700 }}
              >
                {initial(m.name)}
              </Avatar>
              <Box sx={{ minWidth: 0, flex: 1, textAlign: "left" }}>
                <Typography typography="s2" fontWeight={600} noWrap>
                  {m.name}
                  {m.noAccess ? (
                    <Box
                      component="span"
                      sx={{ color: "text.disabled", fontWeight: 400 }}
                    >
                      {" · No access"}
                    </Box>
                  ) : null}
                </Typography>
                {m.email && (
                  <Typography typography="s3" color="text.disabled" noWrap>
                    {m.email}
                  </Typography>
                )}
              </Box>
            </Stack>
          </ButtonBase>
        ))
      )}
    </Paper>
  </Popper>
);

MentionPicker.propTypes = {
  anchorEl: PropTypes.object,
  open: PropTypes.bool,
  candidates: PropTypes.array.isRequired,
  active: PropTypes.number,
  onPick: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

export default MentionPicker;
