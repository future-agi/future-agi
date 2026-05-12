import PropTypes from "prop-types";
import React, { useMemo, useState, useRef, useEffect } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../develop-detail/AccordianElements";
import { Box, Divider, Typography, useTheme } from "@mui/material";
import "react-json-view-lite/dist/index.css";
import CustomTooltip from "src/components/tooltip";
import { getMarkColor } from "src/utils/utils";
import CustomTooltipImage from "src/components/tooltip/CustomTooltipImage";
import { ShowComponent } from "src/components/show";

function getImageUrlFromData(data) {
  if (data && data?.selected_input_key) {
    const selectedInputKey = data?.selected_input_key;

    let inputKeys = [];
    let inputKeyData = [];
    if (data?.input_types) {
      inputKeys = Object.keys(data?.input_types);
    }
    if (data?.data) {
      inputKeyData = Object.keys(data?.data);
    }

    let format = "";
    let dataFile = "";

    inputKeys.forEach((key) => {
      if (key === selectedInputKey) {
        format = data?.input_types[key];
        dataFile = data?.input_data[selectedInputKey];
      }
    });

    if (format == "image") {
      if (!dataFile) {
        dataFile = data?.data?.image_url || "";
      }
    } else {
      if (!dataFile) {
        inputKeyData.forEach((key) => {
          if (key === selectedInputKey) {
            format = "text";
            dataFile = data?.data[key];
          }
        });
      }
    }

    return { format: format || "text", dataFile: dataFile || "" };
  } else if (data?.cell_metadata && data?.cell_metadata?.selected_input_key) {
    const selectedInputKey = data?.cell_metadata?.selected_input_key;

    let inputKeys = [];
    let inputKeyData = [];
    if (data?.cell_metadata?.input_types) {
      inputKeys = Object.keys(data?.cell_metadata?.input_types);
    }
    if (data?.data) {
      inputKeyData = Object.keys(data?.data);
    }

    let format = "";
    let dataFile = "";

    inputKeys.forEach((key) => {
      if (key === selectedInputKey) {
        format = data?.cell_metadata?.input_types[key];
        dataFile = data?.cell_metadata?.input_data[selectedInputKey];
      }
    });

    if (format == "image") {
      if (!dataFile) {
        dataFile = data?.data?.image_url || "";
      }
    } else {
      if (!dataFile) {
        inputKeyData.forEach((key) => {
          if (key === selectedInputKey) {
            format = "text";
            dataFile = data?.data[key];
          }
        });
      }
    }

    return { format: format || "text", dataFile: dataFile || "" };
  } else {
    return null;
  }
}

const ImageWithOverlay = ({ imageUrl, value, boxWidth, boxHeight }) => {
  const canvasRef = useRef(null);
  const imageRef = useRef(null);
  const [hoveredData, setHoveredData] = useState(null);
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 });

  const parseRgba = (rgba) => {
    const matches = rgba.match(/rgba?\((\d+), (\d+), (\d+), (\d+\.?\d*)\)/);
    if (matches) {
      return [
        parseInt(matches[1]),
        parseInt(matches[2]),
        parseInt(matches[3]),
        parseFloat(matches[4]),
      ];
    }
    return [0, 0, 0, 1]; // Default to black if parsing fails
  };

  const handleMouseEnter = (reason, mouseX, mouseY) => {
    setHoveredData(reason);
    setTooltipPosition({ x: mouseX, y: mouseY });
  };

  const handleMouseLeave = () => {
    setHoveredData(null);
  };

  const isInsideRect = (x, y, topLeft, bottomRight) => {
    const scaleX = boxWidth / imageRef.current.naturalWidth;
    const scaleY = boxHeight / imageRef.current.naturalHeight;

    const scaledTopLeft = [topLeft[0] * scaleX, topLeft[1] * scaleY];
    const scaledBottomRight = [
      bottomRight[0] * scaleX,
      bottomRight[1] * scaleY,
    ];

    return (
      x >= scaledTopLeft[0] &&
      x <= scaledBottomRight[0] &&
      y >= scaledTopLeft[1] &&
      y <= scaledBottomRight[1]
    );
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const image = imageRef.current;

    if (image && ctx) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // If value is empty, don't draw any highlights
      if (!value || value.length === 0) {
        return;
      }

      image.onload = () => {
        const originalWidth = image.naturalWidth;
        const originalHeight = image.naturalHeight;

        // Update canvas size to match the image size in the container
        canvas.width = boxWidth;
        canvas.height = boxHeight;

        const scaleX = boxWidth / originalWidth;
        const scaleY = boxHeight / originalHeight;

        // Keep track of the highlighted areas to detect overlap
        const highlightAreas = [];

        value.forEach((item) => {
          const { topLeft, bottomRight } = item.orgPatch.coordinates;
          const weight = item.weight;
          const opacity = 0.2; // Base opacity for highlights
          const color = getMarkColor(weight, false, opacity);

          // Scale the coordinates based on image size
          const scaledTopLeft = [topLeft[0] * scaleX, topLeft[1] * scaleY];
          const scaledBottomRight = [
            bottomRight[0] * scaleX,
            bottomRight[1] * scaleY,
          ];

          const width = scaledBottomRight[0] - scaledTopLeft[0];
          const height = scaledBottomRight[1] - scaledTopLeft[1];

          // Check for overlap with existing highlights and adjust opacity
          let overlap = false;
          highlightAreas.forEach((area) => {
            if (
              scaledTopLeft[0] < area.bottomRight[0] &&
              scaledBottomRight[0] > area.topLeft[0] &&
              scaledTopLeft[1] < area.bottomRight[1] &&
              scaledBottomRight[1] > area.topLeft[1]
            ) {
              overlap = true;
            }
          });

          highlightAreas.push({
            topLeft: scaledTopLeft,
            bottomRight: scaledBottomRight,
          });

          // Change color for overlapping areas (Darker shades)
          let adjustedColor = color;
          if (overlap) {
            // Make color darker or more transparent if there's overlap
            adjustedColor = getMarkColor(weight, true, 0.1); // A function that can provide a darker color variant
          }

          // Apply gradient for overlap effect
          if (overlap) {
            const gradient = ctx.createLinearGradient(
              scaledTopLeft[0],
              scaledTopLeft[1],
              scaledBottomRight[0],
              scaledBottomRight[1],
            );

            // Adjust the adjustedColor to RGBA format directly with alpha values
            // eslint-disable-next-line no-unused-vars
            const [r, g, b, a] = parseRgba(adjustedColor); // Assuming adjustedColor is in rgba format

            // Use RGBA with varying alpha for the gradient stops
            gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.2)`); // Transparent start
            gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0.7)`); // Full color end

            ctx.fillStyle = gradient;
          } else {
            ctx.fillStyle = adjustedColor;
          }

          // Draw the rectangle with adjusted styles
          ctx.fillRect(scaledTopLeft[0], scaledTopLeft[1], width, height);

          // Make the outline thicker for better visibility
          ctx.strokeStyle = adjustedColor;
          ctx.lineWidth = 4; // Thicker stroke
          ctx.strokeRect(scaledTopLeft[0], scaledTopLeft[1], width, height);
        });
      };
    }
  }, [value, boxWidth, boxHeight]);

  return (
    <Box sx={{ position: "relative", display: "inline-block" }}>
      <img
        ref={imageRef}
        src={imageUrl}
        alt="Overlayed"
        style={{ display: "block", width: boxWidth, height: boxHeight }}
      />
      <canvas
        ref={canvasRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          pointerEvents: "auto",
        }}
        onMouseMove={(e) => {
          const canvasRect = canvasRef.current.getBoundingClientRect();
          const mouseX = e.clientX - canvasRect.left;
          const mouseY = e.clientY - canvasRect.top;

          value.forEach((item) => {
            const { topLeft, bottomRight } = item.orgPatch.coordinates;

            if (isInsideRect(mouseX, mouseY, topLeft, bottomRight)) {
              handleMouseEnter(item.reason, mouseX, mouseY);
            }
          });
        }}
        onMouseLeave={handleMouseLeave}
      />

      <CustomTooltipImage
        show={Boolean(hoveredData)}
        title={hoveredData}
        x={tooltipPosition.x}
        y={tooltipPosition.y}
      />
    </Box>
  );
};

const ErrorLocalizeCard = ({ value, datapoint, column, sx = {} }) => {
  const [hoveredData, setHoveredData] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const imageKey = `${datapoint?.selectedInputKey ? datapoint?.selectedInputKey : datapoint?.cellMetadata?.selectedInputKey}-${value?.length}`;
  const handleShowMore = (value) => {
    setExpanded(value);
  };
  const theme = useTheme();
  const imageData = useMemo(() => getImageUrlFromData(datapoint), [datapoint]);
  const isImage = imageData?.format === "image";
  const isText = imageData?.format === "text";
  const showMoreCondition =
    !datapoint?.data?.imageUrl &&
    imageData?.dataFile?.length > 150 &&
    !expanded;

  const showLessCondition =
    !datapoint?.data?.imageUrl && imageData?.dataFile?.length > 150 && expanded;

  useEffect(() => {
    setExpanded(false);
    setHoveredData(null);
  }, [value, datapoint, column]);

  const renderLocalizerDetails = (item) => {
    const reason = item?.reason;
    const improvement = item?.improvement;
    const rankReason = item?.rank_reason || item?.rankReason;

    if (!reason && !improvement && !rankReason) return null;

    return (
      <Box
        sx={{
          mt: 1,
          p: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          backgroundColor: "background.paper",
        }}
      >
        {reason && (
          <Box sx={{ mb: improvement || rankReason ? 1 : 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              color="text.primary"
              sx={{ display: "block", mb: 0.25 }}
            >
              Reason
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {reason}
            </Typography>
          </Box>
        )}
        {improvement && (
          <Box sx={{ mb: rankReason ? 1 : 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              color="text.primary"
              sx={{ display: "block", mb: 0.25 }}
            >
              Suggested fix
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {improvement}
            </Typography>
          </Box>
        )}
        {rankReason && (
          <Box>
            <Typography
              variant="caption"
              fontWeight={600}
              color="text.primary"
              sx={{ display: "block", mb: 0.25 }}
            >
              Severity
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {rankReason}
            </Typography>
          </Box>
        )}
      </Box>
    );
  };

  const highlightText = (text, startEx, endEx, weight, data) => {
    if (!text) return "";
    let fullText = text;
    const handleMouseEnter = (reason) => {
      setHoveredData(reason);
    };

    const handleMouseLeave = () => {
      setHoveredData(null);
    };

    if (endEx > fullText?.length) {
      endEx = startEx + fullText?.length;
    }

    if (startEx < 0 || endEx > fullText?.length || startEx >= endEx) {
      if (expanded) {
        return fullText;
      } else {
        if (!(fullText?.length < 200)) {
          fullText = `${fullText?.slice(0, 200)}...`;
        }
      }
    }

    let beforeHighlight = fullText.slice(0, startEx);
    const highlighted = fullText.slice(startEx, endEx);
    let afterHighlight = fullText.slice(endEx);

    if (!expanded) {
      if (startEx > 100) {
        const truncatedBefore = beforeHighlight.slice(-20);
        beforeHighlight = `...${truncatedBefore}`;
      }
      if (endEx > 100 && startEx !== 0) {
        const truncatedAfter = afterHighlight.slice(0, 40);
        afterHighlight = `${truncatedAfter}...`;
      }
    }

    return (
      <>
        {beforeHighlight}
        <CustomTooltip
          show={Boolean(hoveredData)}
          title={
            <Box sx={{ maxHeight: "100px", overflow: "auto" }}>
              {data?.reason}
            </Box>
          }
          enterDelay={500}
        >
          <span
            onMouseEnter={() => handleMouseEnter(data.reason)}
            onMouseLeave={handleMouseLeave}
            style={{
              backgroundColor: getMarkColor(weight),
              fontWeight: "",
              display: "inline",
            }}
          >
            {highlighted}
          </span>
        </CustomTooltip>
        {afterHighlight}
      </>
    );
  };

  const renderTextWithAllHighlights = (text, highlights) => {
    if (!text || !highlights || highlights.length === 0) {
      return text;
    }

    const getStartIdx = (highlight) =>
      highlight?.orgSen?.startIdx ?? highlight?.orgSen?.start_idx ?? 0;
    const getEndIdx = (highlight) =>
      highlight?.orgSen?.endIdx ??
      highlight?.orgSen?.end_idx ??
      getStartIdx(highlight);

    const sortedHighlights = [...highlights].sort(
      (a, b) => getStartIdx(a) - getStartIdx(b),
    );

    const result = [];
    let lastIndex = 0;

    sortedHighlights.forEach((highlight, index) => {
      const startIdx = getStartIdx(highlight);
      const endIdx = getEndIdx(highlight);

      if (startIdx > lastIndex) {
        result.push(text.slice(lastIndex, startIdx));
      }

      const highlightedText = text.slice(startIdx, endIdx);
      result.push(
        <CustomTooltip
          key={`highlight-${index}`}
          show={Boolean(hoveredData)}
          title={
            <Box sx={{ maxHeight: "100px", overflow: "auto" }}>
              {highlight?.reason}
            </Box>
          }
          enterDelay={500}
        >
          <span
            onMouseEnter={() => setHoveredData(highlight.reason)}
            onMouseLeave={() => setHoveredData(null)}
            style={{
              backgroundColor: getMarkColor(highlight.weight),
              fontWeight: "",
              display: "inline",
            }}
          >
            {highlightedText}
          </span>
        </CustomTooltip>,
      );

      lastIndex = endIdx;
    });

    // Add remaining text after the last highlight
    if (lastIndex < text.length) {
      result.push(text.slice(lastIndex));
    }

    return result;
  };

  return (
    <Accordion
      defaultExpanded
      disableGutters
      sx={{
        borderWidth: "1px",
      }}
    >
      <AccordionSummary
        sx={{ color: "text.primary", fontWeight: "500", fontSize: "14px" }}
      >
        {column}
      </AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box
          sx={{
            padding: 2,
            paddingTop: 0,
          }}
        >
          <Box>
            {!isImage ? (
              <Box
                sx={{
                  paddingX: theme.spacing(1.5),
                  paddingY: theme.spacing(1.5),
                  backgroundColor: "background.neutral",
                  borderRadius: "8px",
                  overflowWrap: "break-word",
                }}
              >
                {value.length > 0 ? (
                  <>
                    {!expanded ? (
                      // Show individual highlights when not expanded
                      value.map((i, index) => (
                        <React.Fragment key={i.unitKey || i.unit_key || index}>
                          <Typography
                            sx={{ marginY: theme.spacing(1.5), ...sx }}
                            variant="body2"
                          >
                            <Box
                              sx={{
                                display: "-webkit-box",
                                maxHeight: "5.5em",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                WebkitLineClamp: 5,
                                position: "relative",
                                typography: "s2",
                                lineHeight: 2,
                              }}
                            >
                              {highlightText(
                                datapoint?.input_data[
                                  datapoint.selected_input_key
                                ],
                                i?.orgSen?.startIdx ?? i?.orgSen?.start_idx,
                                i?.orgSen?.endIdx ?? i?.orgSen?.end_idx,
                                i.weight,
                                i,
                              )}
                            </Box>
                          </Typography>
                          {renderLocalizerDetails(i)}
                          {value.length > 1 && index < value.length - 1 && (
                            <Divider />
                          )}
                        </React.Fragment>
                      ))
                    ) : (
                      <>
                        <Typography
                          sx={{
                            marginY: theme.spacing(1.5),
                            overflowY: "auto",
                            ...sx,
                          }}
                          variant="s2"
                        >
                          {renderTextWithAllHighlights(
                            datapoint?.input_data[datapoint.selected_input_key],
                            value,
                          )}
                        </Typography>
                        <Box
                          sx={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 1,
                          }}
                        >
                          {value.map((item, index) => (
                            <React.Fragment
                              key={item.unitKey || item.unit_key || index}
                            >
                              {renderLocalizerDetails(item)}
                            </React.Fragment>
                          ))}
                        </Box>
                      </>
                    )}
                    <ShowComponent condition={showMoreCondition}>
                      <Box
                        sx={{
                          padding: theme.spacing(0),
                          textAlign: "right",
                          paddingX: theme.spacing(2),
                          paddingY: theme.spacing(1.5),
                          backgroundColor: "background.neutral",
                          borderRadius: "8px",
                        }}
                      >
                        <Typography
                          sx={{
                            color: "text.primary",
                            typography: "s2",
                            fontWeight: "700",
                            textDecoration: "underline",
                            cursor: "pointer",
                          }}
                          onClick={() => handleShowMore(true)}
                        >
                          Show More
                        </Typography>
                      </Box>
                    </ShowComponent>
                    <ShowComponent condition={showLessCondition}>
                      <Box
                        sx={{
                          padding: theme.spacing(0),
                          textAlign: "right",
                          paddingX: theme.spacing(2),
                          paddingY: theme.spacing(1.5),
                          backgroundColor: "background.neutral",
                          borderRadius: "8px",
                        }}
                      >
                        <Typography
                          sx={{
                            color: "text.primary",
                            typography: "s2",
                            fontWeight: "700",
                            textDecoration: "underline",
                            cursor: "pointer",
                          }}
                          onClick={() => handleShowMore(false)}
                        >
                          Show Less
                        </Typography>
                      </Box>
                    </ShowComponent>
                  </>
                ) : (
                  <>
                    <Typography
                      sx={{ marginY: theme.spacing(2) }}
                      variant="body2"
                    >
                      <Box
                        sx={{
                          display: "-webkit-box",
                          maxHeight: expanded ? "none" : "5.5em",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          WebkitLineClamp: expanded ? "none" : 5,
                          position: "relative",
                        }}
                      >
                        {isText
                          ? highlightText(
                              imageData?.dataFile,
                              -1,
                              -1,
                              1,
                              datapoint,
                            )
                          : ""}
                      </Box>
                    </Typography>
                    {showMoreCondition && (
                      <Box
                        sx={{
                          padding: theme.spacing(0),
                          textAlign: "right",
                          paddingX: theme.spacing(2),
                          paddingY: theme.spacing(1.5),
                          backgroundColor: "background.neutral",
                          borderRadius: "8px",
                        }}
                      >
                        <Typography
                          sx={{
                            color: "text.primary",
                            fontSize: "14px",
                            fontWeight: "700",
                            textDecoration: "underline",
                            cursor: "pointer",
                          }}
                          onClick={handleShowMore}
                        >
                          Show More
                        </Typography>
                      </Box>
                    )}
                  </>
                )}
              </Box>
            ) : (
              <Box
                sx={{
                  paddingX: theme.spacing(2),
                  paddingY: theme.spacing(1.5),
                  backgroundColor: "background.neutral",
                  borderRadius: "8px",
                  overflowWrap: "break-word",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    width: "100%",
                    height: "auto",
                  }}
                >
                  <ImageWithOverlay
                    key={imageKey}
                    imageUrl={imageData?.dataFile}
                    value={value}
                    weight={value?.[0]?.weight}
                    boxWidth={400}
                    boxHeight={400}
                  />
                </Box>
              </Box>
            )}
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

ErrorLocalizeCard.propTypes = {
  value: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  column: PropTypes.string,
  sx: PropTypes.object,
  datapoint: PropTypes.object,
};

ImageWithOverlay.propTypes = {
  imageUrl: PropTypes.string.isRequired,
  coordinates: PropTypes.shape({
    topLeft: PropTypes.arrayOf(PropTypes.number).isRequired,
    bottomRight: PropTypes.arrayOf(PropTypes.number).isRequired,
  }).isRequired,
  value: PropTypes.array,
  boxWidth: PropTypes.number,
  boxHeight: PropTypes.number,
  weight: PropTypes.number,
  data: PropTypes.object,
};

export default ErrorLocalizeCard;
