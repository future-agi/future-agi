import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, Stack, TextField } from "@mui/material";

export default function SkillFilesPanel({ files, label }) {
  const [path, setPath] = useState("");
  const shown = files.find((file) => file.path === path) || files[0];
  return (
    <Stack spacing={1}>
      <TextField
        select
        size="small"
        label={label}
        value={shown?.path || ""}
        onChange={(event) => setPath(event.target.value)}
      >
        {files.map((file) => (
          <MenuItem key={file.path} value={file.path}>
            {file.path}
          </MenuItem>
        ))}
      </TextField>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          typography: "s1",
          fontFamily: "monospace",
          whiteSpace: "pre-wrap",
          maxHeight: 320,
          overflow: "auto",
        }}
      >
        {shown?.content || ""}
      </Box>
    </Stack>
  );
}

SkillFilesPanel.propTypes = {
  files: PropTypes.arrayOf(
    PropTypes.shape({
      path: PropTypes.string.isRequired,
      content: PropTypes.string.isRequired,
    }),
  ).isRequired,
  label: PropTypes.string.isRequired,
};
