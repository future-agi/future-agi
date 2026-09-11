import React from "react";
import { Avatar, Box, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { format } from "date-fns";
import stringAvatar from "src/utils/stringAvatar";
import { isBlackBackgroundLogo } from "src/components/custom-model-dropdown/common";

export const CustomCellRender = ({ value, data, column }) => {
  const theme = useTheme();
  const columnId = column.colDef.columnId;
  const modelDetail = data?.model_detail;

  switch (columnId) {
    case "Collaborators":
      return (
        <Box
          sx={{
            height: "100%",
            width: "fit-content",
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
            gap: theme.spacing(1),
          }}
        >
          {data?.collaborators?.length > 0 && (
            <>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: theme.spacing(0.5),
                }}
              >
                <Avatar
                  variant="rounded"
                  {...stringAvatar(data.collaborators[0]?.name)}
                  sx={{
                    width: theme.spacing(3),
                    height: theme.spacing(3),
                    bgcolor: "background.neutral",
                    color: "pink.500",
                  }}
                />
                <Typography typography="s2" fontWeight={"fontWeightRegular"}>
                  {data.collaborators[0].email}
                </Typography>
              </Box>
              {data.collaborators.length > 1 && (
                <Typography typography="s2" fontWeight={"fontWeightRegular"}>
                  +{data.collaborators.length - 1}
                </Typography>
              )}
            </>
          )}
        </Box>
      );
    case "Models":
      return (
        <Box
          sx={{
            height: "100%",
            width: "fit-content",
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
          }}
        >
          {value && typeof value === "string" ? (
            <IconButton
              sx={{
                borderRadius: theme.spacing(0.5),
                backgroundColor: "background.paper",
                border: "1px solid",
                borderColor: "divider",
                paddingY: theme.spacing(0.5),
                paddingX: theme.spacing(1),
                gap: theme.spacing(1),
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-start",
                maxWidth: "200px",
                minWidth: "100px",
              }}
            >
              {modelDetail?.logo_url ? (
                <Box
                  component="img"
                  src={modelDetail?.logo_url}
                  alt={modelDetail?.model_name}
                  sx={{
                    width: theme.spacing(2),
                    height: theme.spacing(2),
                    objectFit: "cover",
                    ...(theme.palette.mode === "dark" &&
                      isBlackBackgroundLogo(modelDetail?.logo_url) && {
                        filter: "invert(1) brightness(2)",
                      }),
                  }}
                />
              ) : null}
              <Typography
                typography="s3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                sx={{
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                }}
              >
                {value}
              </Typography>
            </IconButton>
          ) : (
            `-`
          )}
        </Box>
      );
    case "date":
      return (
        <Box
          sx={{
            height: "100%",
            width: "fit-content",
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
          }}
        >
          <Typography typography="s1" fontWeight={"fontWeightRegular"}>
            {value ? format(new Date(value), "dd/MM/yyyy, h:mm aaa") : ""}
          </Typography>
        </Box>
      );
    default:
      return (
        <Box
          sx={{
            height: "100%",
            width: "fit-content",
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
          }}
        >
          <Typography typography="s1" fontWeight={"fontWeightRegular"}>
            {value}
          </Typography>
        </Box>
      );
  }
};

CustomCellRender.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
  column: PropTypes.any,
};
