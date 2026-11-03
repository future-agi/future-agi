import React, { useState } from "react";
import { Box, Typography, Button } from "@mui/material";
import PropTypes from "prop-types";

const FileUploader = ({ setValue }) => {
  const [fileName, setFileName] = useState(""); // Default file name

  function onReaderLoad(event) {
    setValue("jsonData", event?.target?.result);
  }

  // Handle file selection
  const handleFileChange = (event) => {
    const file = event?.target?.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = onReaderLoad;
      reader.readAsText(file);
      setFileName(file?.name); // Update file name in UI
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "7px",
        borderRadius: "8px",
        backgroundColor: "background.neutral",
        width: "100%",
        // maxWidth: "400px",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <Typography
          sx={{
            fontWeight: 600,
            fontSize: "12px",
            lineHeight: "24px",
            alignItems: "center",
          }}
        >
          JSON
        </Typography>
        <Typography variant="body1" sx={{ fontWeight: "500" }}>
          {fileName}
        </Typography>
      </Box>
      <Button variant="text" sx={{ color: "primary.main" }}>
        Update new file
        <input
          type="file"
          accept=".json"
          style={{
            position: "absolute",
            opacity: 0,
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            cursor: "pointer",
          }}
          onChange={handleFileChange}
        />
      </Button>
    </Box>
  );
};

export default FileUploader;

FileUploader.propTypes = {
  setValue: PropTypes.any,
};
