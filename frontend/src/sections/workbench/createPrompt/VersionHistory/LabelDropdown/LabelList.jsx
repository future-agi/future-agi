import { Box, CircularProgress, MenuItem, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import PromptLabel from "./PromptLabel";

const LabelList = ({
  labels,
  isPending,
  isFetchingNextPage,
  isLabelSelected,
  handleSelect,
  version,
  fetchNextPage,
}) => {
  const scrollRef = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  return (
    <Box
      ref={scrollRef}
      sx={{
        mt: 0.75,
        p: 0.5,
        maxHeight: 176,
        overflowY: "auto",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
      }}
    >
      {isPending ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 1.5 }}>
          <CircularProgress size={18} />
        </Box>
      ) : labels.length === 0 ? (
        <Typography
          variant="body2"
          color="text.secondary"
          textAlign="center"
          py={1.5}
        >
          No labels found
        </Typography>
      ) : (
        labels.map((label) => {
          const selected = isLabelSelected(label.id);
          return (
            <MenuItem
              key={label.id}
              onClick={() => handleSelect(label)}
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 1,
                backgroundColor: selected ? "action.hover" : "transparent",
                "&:hover": {
                  backgroundColor: selected
                    ? "action.selected"
                    : "action.hover",
                },
                borderRadius: "6px",
                mb: 0.25,
                px: 1,
                py: 0.5,
                minHeight: "unset",
              }}
            >
              <PromptLabel
                viewOnly={true}
                name={label.name}
                id={label.id}
                version={version}
              />
              {selected && (
                <Iconify
                  icon="mdi:check"
                  sx={{ width: 16, height: 16, color: "text.secondary" }}
                />
              )}
            </MenuItem>
          );
        })
      )}

      {isFetchingNextPage && (
        <Box sx={{ display: "flex", justifyContent: "center", p: 0.75 }}>
          <CircularProgress size={16} />
        </Box>
      )}
    </Box>
  );
};

LabelList.propTypes = {
  labels: PropTypes.array.isRequired,
  isPending: PropTypes.bool.isRequired,
  isFetchingNextPage: PropTypes.bool.isRequired,
  isLabelSelected: PropTypes.func.isRequired,
  handleSelect: PropTypes.func.isRequired,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  fetchNextPage: PropTypes.func.isRequired,
};

export default LabelList;
