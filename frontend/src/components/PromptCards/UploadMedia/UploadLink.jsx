import { Box, Button, TextField } from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useFieldArray } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import { z } from "zod";
import { useSnackbar } from "src/components/snackbar";

const UploadLink = ({ control }) => {
  const { append, fields } = useFieldArray({ control, name: "links" });
  const [url, setUrl] = useState("");
  const { enqueueSnackbar } = useSnackbar();

  const handleAddLink = () => {
    const urlSchema = z.string().url();
    const result = urlSchema.safeParse(url);

    if (result.success) {
      append({ id: getRandomId(), url, name: `Image ${fields.length + 1}` });
      setUrl("");
    } else {
      enqueueSnackbar("Enter a valid URL", { variant: "error" });
    }
  };

  return (
    <Box sx={{ display: "flex", gap: 2 }}>
      <TextField
        label="Link"
        placeholder="https://www.google.com"
        fullWidth
        size="small"
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            handleAddLink();
          }
        }}
        sx={{ height: "38px" }}
        value={url}
        onChange={(e) => setUrl(e.target.value)}
      />
      <Button
        variant="outlined"
        color="primary"
        sx={{
          minWidth: "90px",
        }}
        onClick={handleAddLink}
      >
        Upload
      </Button>
    </Box>
  );
};

UploadLink.propTypes = {
  control: PropTypes.object,
};

export default UploadLink;
