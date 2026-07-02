import {
  Box,
  Button,
  CircularProgress,
  ClickAwayListener,
  MenuItem,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import PromptLabel from "./PromptLabel";

// Separate Popper Content Component
const LabelPopperContent = ({
  labels,
  isPending,
  isFetchingNextPage,
  isLabelSelected,
  handleSelect,
  showCreateForm,
  setShowCreateForm,
  newLabelName,
  setNewLabelName,
  handleCreateLabel,
  isCreatingLabel,
  onClose,
  version,
  fetchNextPage,
}) => {
  const scrollRef = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);
  return (
    <ClickAwayListener onClickAway={onClose}>
      <Paper
        sx={{
          mt: 0.5,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: "divider",
          maxHeight: 360,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Labels List */}
        <Box
          ref={scrollRef}
          sx={{
            p: 1,
            maxHeight: 200,
            overflowY: "auto",
            flex: 1,
          }}
        >
          {isPending ? (
            <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
              <CircularProgress size={20} />
            </Box>
          ) : labels.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              textAlign="center"
              py={2}
            >
              No labels found
            </Typography>
          ) : (
            labels.map((label) => (
              <MenuItem
                key={label.id}
                onClick={() => handleSelect(label)}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  backgroundColor: isLabelSelected(label.id)
                    ? "background.neutral"
                    : "transparent",
                  "&:hover": {
                    backgroundColor: isLabelSelected(label.id)
                      ? "background.neutral"
                      : "action.hover",
                  },
                  borderRadius: "6px",
                  mb: 0.5,
                  py: 0.75,
                }}
              >
                <PromptLabel
                  viewOnly={true}
                  name={label.name}
                  id={label.id}
                  version={version}
                />
              </MenuItem>
            ))
          )}

          {isFetchingNextPage && (
            <Box sx={{ display: "flex", justifyContent: "center", p: 1 }}>
              <CircularProgress size={16} />
            </Box>
          )}
        </Box>

        <Box
          sx={{
            p: 1,
            borderTop: "1px solid",
            borderColor: "background.neutral",
          }}
        >
          <ShowComponent condition={!showCreateForm}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                color: "primary.main",
                cursor: "pointer",
                py: 0.5,
                px: 1,
                borderRadius: "6px",
                "&:hover": {
                  bgcolor: (theme) =>
                    theme.palette.mode === "dark"
                      ? theme.palette.action.hover
                      : theme.palette.primary.lighter,
                },
              }}
              onClick={() => setShowCreateForm(true)}
            >
              <SvgColor
                src="/assets/icons/ic_add.svg"
                sx={{ width: 16, height: 16 }}
              />
              <Typography typography="s3" fontWeight={500}>
                Add custom label
              </Typography>
            </Box>
          </ShowComponent>

          <ShowComponent condition={showCreateForm}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 1,
                width: "100%",
              }}
            >
              <TextField
                size="small"
                fullWidth
                autoFocus
                placeholder="Enter label name"
                value={newLabelName}
                onChange={(e) => setNewLabelName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    handleCreateLabel();
                  }
                }}
                disabled={isCreatingLabel}
                sx={{
                  "& .MuiOutlinedInput-root": {
                    fontSize: "14px",
                  },
                }}
              />
              <Box sx={{ display: "flex", gap: 1 }}>
                <Button
                  size="small"
                  variant="outlined"
                  fullWidth
                  onClick={() => {
                    setShowCreateForm(false);
                    setNewLabelName("");
                  }}
                  disabled={isCreatingLabel}
                >
                  <Typography variant="s3" fontWeight={500}>
                    Cancel
                  </Typography>
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  fullWidth
                  onClick={handleCreateLabel}
                  disabled={!newLabelName.trim() || isCreatingLabel}
                  startIcon={isCreatingLabel && <CircularProgress size={14} />}
                >
                  <Typography
                    variant="s3"
                    fontWeight={500}
                    color="primary.contrastText"
                  >
                    {isCreatingLabel ? "Saving..." : "Save"}
                  </Typography>
                </Button>
              </Box>
            </Box>
          </ShowComponent>
        </Box>
      </Paper>
    </ClickAwayListener>
  );
};

LabelPopperContent.propTypes = {
  labels: PropTypes.array.isRequired,
  isPending: PropTypes.bool.isRequired,
  isFetchingNextPage: PropTypes.bool.isRequired,
  isLabelSelected: PropTypes.func.isRequired,
  handleSelect: PropTypes.func.isRequired,
  showCreateForm: PropTypes.bool.isRequired,
  setShowCreateForm: PropTypes.func.isRequired,
  newLabelName: PropTypes.string.isRequired,
  setNewLabelName: PropTypes.func.isRequired,
  handleCreateLabel: PropTypes.func.isRequired,
  isCreatingLabel: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  fetchNextPage: PropTypes.func.isRequired,
};

export default LabelPopperContent;
