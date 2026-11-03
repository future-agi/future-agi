import { Box, Button, TextField, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { action } from "src/theme/palette";

const EditablePromptItem = ({
  item,
  index,
  conversation,
  setConversation,
  handleCancelEdit,
  handleUpdatePrompt,
}) => {
  const theme = useTheme();
  const handleChange = (e) => {
    const updated = [...conversation];
    updated[index].prompt = e.target.value;
    setConversation(updated);
  };

  return (
    <Box sx={{ position: "relative", width: "100%" }}>
      <TextField
        multiline
        minRows={1}
        maxRows={2}
        fullWidth
        value={item.prompt}
        onChange={handleChange}
        sx={{
          background: action.hover,
          borderRadius: theme.spacing(1.4),
          width: "100%",
          marginBottom: theme.spacing(1),
          paddingBottom: theme.spacing(3),
          height: "auto",

          "& .MuiOutlinedInput-root": {
            "& fieldset": {
              border: "none",
            },
            "&:hover fieldset": {
              border: "none",
            },
            "&.Mui-focused fieldset": {
              border: "none",
            },
          },
          "& .MuiInputBase-input": {},
        }}
      />
      <Box
        sx={{
          position: "absolute",
          bottom: theme.spacing(0.375),
          right: theme.spacing(0.75),
          fontSize: "0.75rem",
          padding: 0.6,
          paddingBottom: 1.3,
        }}
      >
        <Button
          aria-label="cancel-prompt-edit"
          size="small"
          onClick={handleCancelEdit}
          sx={{
            minWidth: "50px",
            margin: 0.5,
            color: "text.primary",
            background: "action.selected",
          }}
        >
          Cancel
        </Button>
        <Button
          aria-label="update-prompt"
          size="small"
          variant="contained"
          color="primary"
          onClick={() => handleUpdatePrompt(index, item.prompt)}
          sx={{
            minWidth: "45px",
          }}
        >
          Update
        </Button>
      </Box>
    </Box>
  );
};

EditablePromptItem.propTypes = {
  item: PropTypes.object,
  index: PropTypes.number,
  conversation: PropTypes.array,
  setConversation: PropTypes.func,
  handleCancelEdit: PropTypes.func,
  handleUpdatePrompt: PropTypes.func,
};

export default EditablePromptItem;
