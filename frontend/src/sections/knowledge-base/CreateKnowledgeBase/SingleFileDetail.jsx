import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import RenderFileIcons from "src/components/RenderFileIcons/RenderFileIcons";

const SingleFileDetail = ({ file, fileSize, deleteFile, isLoading }) => {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: file.status !== "error" ? "divider" : "status.danger.fg",
        borderRadius: "8px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        backgroundColor: "background.paper",
        "&:hover": {
          backgroundColor: "background.neutral",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: "12px",
          alignItems: "flex-start",
        }}
      >
        <Box>
          <IconButton
            sx={{
              padding: 0,
              minWidth: 22,
              maxWidth: 22,
              maxHeight: 22,
            }}
          >
            {RenderFileIcons(file.item.type)}
          </IconButton>
        </Box>
        <Box sx={{ flex: 1 }}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            sx={{ wordBreak: "break-all" }}
          >
            {file.item.name}
          </Typography>
          <Box sx={{ display: "flex", gap: "8px" }}>
            <Box>
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color="text.disabled"
              >
                {fileSize}
              </Typography>
            </Box>
            {/* <Box>
              {file.status === "error" ? (
                <Typography
                  variant="s2"
                  fontWeight={"fontWeightMedium"}
                  color="status.danger.fg"
                  sx={{ cursor: "pointer", textDecoration: "underline" }}
                >
                  Try again
                </Typography>
              ) : (
                <Box
                  sx={{
                    width: "10px",
                    height: "10px",
                    marginTop: "8px",
                    borderRadius: "100%",
                    backgroundColor:
                      file.status === "success" ? "green.500" : "divider",
                  }}
                />
              )}
            </Box> */}
          </Box>
          {file.status === "error" && (
            <Typography
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              sx={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
            >
              <Iconify
                icon="solar:danger-triangle-linear"
                color="status.danger.fg"
                sx={{
                  maxWidth: 16,
                  maxHeight: 16,
                  minWidth: 16,
                  minHeight: 16,
                }}
              />{" "}
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color="text.primary"
                sx={{ wordBreak: "break-all" }}
              >
                {file?.statusReason}
              </Typography>
            </Typography>
          )}
        </Box>
        <Box>
          <IconButton
            disabled={isLoading}
            onClick={() => deleteFile(file.status)}
          >
            {file.status === "success" ? (
              <img
                src={`/assets/icons/custom/delete.svg`}
                alt="Delete Icon"
                style={{
                  minWidth: 16,
                  minHeight: 16,
                  maxWidth: 16,
                  maxHeight: 16,
                }}
              />
            ) : (
              <Iconify
                icon="solar:close-linear"
                color="text.primary"
                width={16}
                height={16}
              />
            )}
          </IconButton>
        </Box>
      </Box>
      {/* <LinearProgress
        variant="determinate"
        value={50}
        sx={{
          height: "4px", // Adjust thickness
          borderRadius: 5, // Rounded corners
          backgroundColor: "background.neutral", // Background color
          "& .MuiLinearProgress-bar": {
            backgroundColor: "primary.main", // Progress bar color
          },
        }}
      /> */}
    </Box>
  );
};

export default SingleFileDetail;

SingleFileDetail.propTypes = {
  file: PropTypes.any,
  fileSize: PropTypes.string,
  deleteFile: PropTypes.func,
  fileType: PropTypes.string,
  isLoading: PropTypes.bool,
};
