import { Box, IconButton, TextField, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CustomTooltip from "../tooltip";
import SvgColor from "../svg-color";
import { PromptCardWrapper } from "./PromptCardStyleComponents";

const PlaceHolderCard = ({ placeholder, onPlaceholderChange, onRemove }) => {
  return (
    <>
      <PromptCardWrapper>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="s1" fontWeight={500}>
            Placeholder
          </Typography>
          {onRemove && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              <CustomTooltip show title="Delete" arrow size="small">
                <IconButton size="small" onClick={onRemove}>
                  <SvgColor
                    src="/assets/icons/ic_delete.svg"
                    sx={{
                      width: "20px",
                      height: "20px",
                      color: "text.primary",
                    }}
                  />
                </IconButton>
              </CustomTooltip>
            </Box>
          )}
        </Box>
        <TextField
          fullWidth
          size="small"
          variant="standard"
          placeholder="Add placeholder here (e.g chat_history)"
          value={placeholder}
          onChange={(e) => onPlaceholderChange(e.target.value)}
          InputProps={{
            disableUnderline: true,
            sx: {
              fontSize: "0.9rem",
            },
          }}
        />
      </PromptCardWrapper>
    </>
  );
};

PlaceHolderCard.propTypes = {
  placeholder: PropTypes.object,
  onPlaceholderChange: PropTypes.func,
  onRemove: PropTypes.func,
};

export default PlaceHolderCard;
