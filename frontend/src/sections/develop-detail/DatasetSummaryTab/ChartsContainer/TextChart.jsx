import { Box, Typography } from "@mui/material";
import React, { useRef, useState, useEffect } from "react";
import PropTypes from "prop-types";

const TextChart = ({ data = [], graphLabels, height = 250 }) => {
  const containerRef = useRef(null);
  const [visibleItems, setVisibleItems] = useState([]);

  useEffect(() => {
    if (!containerRef.current || !data.length) return;

    const containerHeight = containerRef.current.clientHeight;
    const containerWidth = containerRef.current.clientWidth;
    const tempVisible = [];
    let hidden = 0;

    // Create a more accurate clone that matches your component's styling
    const clone = document.createElement("div");
    clone.style.cssText = `
      position: absolute;
      visibility: hidden;
      top: -9999px;
      left: -9999px;
      width: ${containerWidth}px;
      height: max-content;
      display: flex;
      justify-content: center;
      flex-wrap: wrap;
      gap: 10px;
      box-sizing: border-box;
    `;

    document.body.appendChild(clone);

    // Test each item to see if it fits
    for (let i = 0; i < data.length; i++) {
      // Create item element that matches your styling
      const itemEl = document.createElement("div");
      itemEl.style.cssText = `
        background-color: var(--bg-subtle);
        padding: 6px 12px;
        box-sizing: border-box;
        font-family: inherit;
        font-size: 14px;
        font-weight: 600;
        white-space: nowrap;
      `;
      itemEl.textContent = data[i].name;
      clone.appendChild(itemEl);

      // Check if adding this item exceeds the container height
      if (clone.offsetHeight > containerHeight - (height / 10) * 2.5) {
        hidden = data.length - i;
        clone.removeChild(itemEl); // Remove the item that caused overflow
        break;
      }

      tempVisible.push({ ...data[i], isCount: false });
    }

    // If we have hidden items, try to fit the +count
    if (hidden > 0) {
      const plusEl = document.createElement("div");
      plusEl.style.cssText = `
        background-color: var(--bg-subtle);
        padding: 6px 12px;
        box-sizing: border-box;
        font-family: inherit;
        font-size: 14px;
        font-weight: 600;
        white-space: nowrap;
      `;
      plusEl.textContent = `+${hidden}`;
      clone.appendChild(plusEl);

      // If adding +count causes overflow, remove last visible item
      if (clone.offsetHeight > containerHeight) {
        tempVisible.pop();
        hidden++;
        plusEl.textContent = `+${hidden}`;
      }

      // Add the +count as the last visible item
      tempVisible.push({ name: `+${hidden}`, isCount: true });
    }

    document.body.removeChild(clone);
    setVisibleItems(tempVisible);
  }, [data, height]);

  return (
    <Box
      sx={{
        backgroundColor: "status.warning.bg",
        border: "1px solid",
        borderColor: "divider",
        padding: 1.5,
        borderRadius: "4px",
      }}
    >
      <Typography
        typography={"s2"}
        fontWeight={"fontWeightMedium"}
        sx={{ textAlign: "center", marginBottom: "10px" }}
      >
        {graphLabels?.[0]}
      </Typography>
      <Box
        display="flex"
        justifyContent="center"
        alignItems={"flex-start"}
        flexWrap="wrap"
        gap={1.25}
        ref={containerRef}
        height={height - (height / 10) * 2.5}
        overflow="hidden"
      >
        {visibleItems.map((item, index) => (
          <Box
            key={index}
            sx={{
              backgroundColor: "status.warning.bg",
              padding: "6px 12px",
              height: "max-content",
            }}
          >
            <Typography typography={"m2"} fontWeight={"fontWeightSemiBold"}>
              {item.name}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
};

TextChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  height: PropTypes.number,
};

export default TextChart;
