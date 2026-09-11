import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { ANNOTATION_TYPES } from "src/utils/constant.js";
import CustomTooltip from "src/components/tooltip";

const CELL_WRAPPER_STYLE = {
  width: "100%",
  height: "100%",
  display: "flex",
  paddingX: "10px",
  alignItems: "center",
  textOverflow: "ellipsis",
  overflow: "hidden",
  gap: "8px",
  whiteSpace: "nowrap",
};

const NewAnnotationCellRenderer = ({
  annotationType,
  isAverage = false,
  settings = {},
  value,
  justifyContent = "flex-start",
  originType = "voiceObservability",
}) => {
  if (value === null || value === undefined) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          width: "100%",
          px: 1,
          justifyContent: "center",
        }}
      >
        <Typography
          typography="s2_1"
          fontWeight={"fontWeightRegular"}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {"-"}
        </Typography>
      </Box>
    );
  }

  if (annotationType === ANNOTATION_TYPES.NUMERIC) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          width: "100%",
          px: 1,
          justifyContent: "center",
        }}
      >
        <Typography
          typography="s2_1"
          fontWeight={"fontWeightRegular"}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {value}
        </Typography>
      </Box>
    );
  }

  if (annotationType === ANNOTATION_TYPES.THUMBS_UP_DOWN) {
    // avg: value = { thumbs_up: N, thumbs_down: N }
    if (isAverage && value && typeof value === "object") {
      const thumbsUp = value.thumbs_up ?? value.thumbsUp ?? 0;
      const thumbsDown = value.thumbs_down ?? value.thumbsDown ?? 0;
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            width: "100%",
            height: "100%",
            justifyContent: "center",
          }}
        >
          <Iconify icon="octicon:thumbsup-24" color="purple.600" />
          <Typography typography="s2_1">{thumbsUp}</Typography>
          <Iconify icon="octicon:thumbsdown-24" color="red.500" />
          <Typography typography="s2_1">{thumbsDown}</Typography>
        </Box>
      );
    }
    // individual: value = 0/"down" (thumbs down) or 100/"up" (thumbs up)
    const isUp = value === 100 || value === "up";
    const isDown = value === 0 || value === "down";
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          width: "100%",
          height: "100%",
          justifyContent: "center",
        }}
      >
        <ShowComponent condition={isUp}>
          <Iconify
            icon="octicon:thumbsup-24"
            sx={{ bgColor: "purple.o10" }}
            color={"purple.600"}
          />
        </ShowComponent>
        <ShowComponent condition={isDown}>
          <Iconify icon="octicon:thumbsdown-24" color={"red.500"} />
        </ShowComponent>
      </Box>
    );
  }
  if (annotationType === ANNOTATION_TYPES.CATEGORICAL) {
    const containerStyles =
      originType === "Tracing"
        ? CELL_WRAPPER_STYLE
        : {
            display: "flex",
            gap: 0.5,
            width: "100%",
            px: 1,
            padding: "10px",
            maxHeight: "80px",
            overflow: "hidden",
            flexWrap: "wrap",
            alignContent: "flex-start",
            justifyContent,
          };

    if (isAverage && value && typeof value === "object") {
      const averageEntries = Object.entries(value).filter(
        ([, count]) => count !== 0,
      );
      const averageTooltipContent = (
        <Box
          sx={{
            display: "flex",
            gap: 0.5,
            flexWrap: "wrap",
            p: "4px",
            maxWidth: "250px",
          }}
        >
          {averageEntries.map(([label, count], index) => (
            <Box
              key={`${label}-${index}`}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                border: "1px solid",
                borderColor: "purple.o10",
                borderRadius: "4px",
                backgroundColor: "purple.o5",
                px: 1,
                py: 0.5,
                height: "24px",
              }}
            >
              <Typography typography="s2_1">{label}</Typography>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "purple.o20",
                  borderRadius: "50%",
                  minWidth: 20,
                  height: 20,
                  px: 0.5,
                }}
              >
                <Typography
                  typography="s2_1"
                  color="purple.600"
                  sx={{ fontSize: 10, lineHeight: 1 }}
                >
                  {count}
                </Typography>
              </Box>
            </Box>
          ))}
        </Box>
      );
      return (
        <CustomTooltip
          size="small"
          arrow={true}
          show={true}
          title={averageTooltipContent}
        >
          <Box sx={{ ...containerStyles }}>
            {averageEntries.map(([label, count], index) => (
              <Box
                key={`${label}-${index}`}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.75,
                  border: "1px solid",
                  borderColor: "purple.o10",
                  borderRadius: "4px",
                  backgroundColor: "purple.o5",
                  px: 1,
                  py: 0.5,
                  height: "24px",
                }}
              >
                <Typography typography="s2_1">{label}</Typography>

                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    backgroundColor: "purple.o20",
                    borderRadius: "50%",
                    minWidth: 20,
                    height: 20,
                    px: 0.5,
                  }}
                >
                  <Typography
                    typography="s2_1"
                    color="purple.600"
                    sx={{ fontSize: 10, lineHeight: 1 }}
                  >
                    {count}
                  </Typography>
                </Box>
              </Box>
            ))}
          </Box>
        </CustomTooltip>
      );
    }

    const labels = Array.isArray(value)
      ? value
      : String(value)
          .split(",")
          .map((v) => v.trim())
          .filter(Boolean);

    const labelsTooltipContent = (
      <Box
        sx={{
          display: "flex",
          gap: 0.5,
          flexWrap: "wrap",
          p: "4px",
          maxWidth: "250px",
        }}
      >
        {labels.map((label, index) => (
          <Box
            key={`${label}-${index}`}
            sx={{
              border: "1px solid",
              borderColor: "purple.o10",
              borderRadius: "4px",
              backgroundColor: "purple.o5",
              px: 1,
              height: "24px",
            }}
          >
            <Typography typography="s2_1">{label}</Typography>
          </Box>
        ))}
      </Box>
    );

    return (
      <CustomTooltip
        arrow={true}
        size="small"
        show={true}
        title={labelsTooltipContent}
      >
        <Box sx={{ ...containerStyles }}>
          {labels.map((label, index) => (
            <Box
              key={`${label}-${index}`}
              sx={{
                border: "1px solid",
                borderColor: "purple.o10",
                borderRadius: "4px",
                backgroundColor: "purple.o5",
                px: 1,
                height: "24px",
              }}
            >
              <Typography typography="s2_1">{label}</Typography>
            </Box>
          ))}
        </Box>
      </CustomTooltip>
    );
  }

  if (annotationType === ANNOTATION_TYPES.STAR) {
    const noOfStars = settings?.noOfStars ?? 5;
    const filled = parseInt(value) || 0;
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          height: "100%",
          justifyContent,
        }}
      >
        {Array.from({ length: filled }).map((_, index) => (
          <Iconify
            key={index}
            icon="material-symbols:star"
            sx={{ fontSize: "inherit", flexShrink: 0 }}
            width={25}
            color="#FFCD29"
          />
        ))}
        <ShowComponent condition={noOfStars !== undefined}>
          {Array.from({ length: noOfStars - filled }).map((_, index) => (
            <Iconify
              key={index}
              icon="material-symbols:star-outline"
              width={25}
              sx={{ fontSize: "inherit", color: "#FFCD29", flexShrink: 0 }}
            />
          ))}
        </ShowComponent>
      </Box>
    );
  }

  return (
    <CustomTooltip arrow={true} size="small" show={true} title={value}>
      <Box
        sx={{
          width: "100%",
          padding: "10px",
          overflow: "hidden",
        }}
      >
        <Typography
          typography="s2_1"
          sx={
            originType === "Tracing"
              ? {
                  whiteSpace: "normal",
                  wordBreak: "break-word",
                  display: "-webkit-box",
                  WebkitLineClamp: 1,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }
              : {
                  whiteSpace: "normal",
                  wordBreak: "break-word",
                  display: "-webkit-box",
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }
          }
        >
          {value}
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

export default NewAnnotationCellRenderer;
NewAnnotationCellRenderer.propTypes = {
  annotationType: PropTypes.string,
  isAverage: PropTypes.bool,
  value: PropTypes.any,
  settings: PropTypes.object,
  justifyContent: PropTypes.string,
  originType: PropTypes.string,
};
