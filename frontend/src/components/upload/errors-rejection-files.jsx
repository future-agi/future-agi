import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Alert from "@mui/material/Alert";
import Typography from "@mui/material/Typography";

import { fData } from "src/utils/format-number";

import { fileData } from "../file-thumbnail";

// ----------------------------------------------------------------------

export default function RejectionFiles({ fileRejections }) {
  if (!fileRejections.length) {
    return null;
  }

  return (
    <Alert
      severity="error"
      icon={false}
      sx={{
        py: 1,
        px: 2,
        mt: 3,
        textAlign: "left",
        "& .MuiAlert-message": { width: 1 },
      }}
    >
      {fileRejections.map(({ file, errors }) => {
        const { path, size } = fileData(file);

        return (
          <Box key={path} sx={{ my: 1 }}>
            <Typography variant="subtitle2" noWrap>
              {path} - {size ? fData(size) : ""}
            </Typography>

            {errors.map((error) => (
              <Box
                key={error.code}
                component="span"
                sx={{ typography: "caption" }}
              >
                - {error.message}
              </Box>
            ))}
          </Box>
        );
      })}
    </Alert>
  );
}

RejectionFiles.propTypes = {
  fileRejections: PropTypes.array,
};
