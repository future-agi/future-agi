import React, { useState, useRef, useEffect } from "react";
import {
  DndContext,
  useSensor,
  useSensors,
  PointerSensor,
} from "@dnd-kit/core";
import { useDraggable } from "@dnd-kit/core";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material";
import SvgColor from "../svg-color";
import "./ResizablePanels.css";

const Divider = ({
  id,
  orientation = "horizontal",
  iconPosition = 30,
  icon: customIconName = null,
  showIcon = true,
  isDragging = false,
}) => {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({
    id,
  });
  const theme = useTheme();

  const style = transform
    ? {
        transform:
          orientation === "horizontal"
            ? `translate3d(${transform.x * 0}px, 0px, 0)`
            : `translate3d(0px, ${transform.y * 0}px, 0)`,
      }
    : undefined;

  const isHorizontal = orientation === "horizontal";

  const iconName = customIconName || "/assets/icons/custom/grip.svg";

  return (
    <div
      ref={setNodeRef}
      className={`divider-handle ${isHorizontal ? "horizontal" : "vertical"}`}
      style={{
        width: isHorizontal ? (isDragging ? "2px" : "1px") : "auto",
        height: isHorizontal ? "auto" : isDragging ? "2px" : "1px",
        backgroundColor: isDragging
          ? "var(--primary-main)"
          : theme.palette.divider,
        cursor: isHorizontal ? "col-resize" : "row-resize",
        position: "relative",
        top: 0,
        bottom: 0,
        zIndex: 10,
        ...style,
      }}
      {...listeners}
      {...attributes}
      // RS-13: the splitter has a name (dnd-kit makes it a button).
      aria-label="Resize panels"
    >
      <div
        className="divider-line"
        style={{
          width: isHorizontal ? "inherit" : "100%",
          height: isHorizontal ? "100%" : "inherit",
          backgroundColor: "inherit",
          margin: "0 auto",
        }}
      />

      {showIcon && (
        <div
          className="divider-icon"
          style={{
            position: "absolute",
            ...(isHorizontal
              ? {
                  top: "50%",
                  left: "50%",
                  transform: "translate(-50%, -50%)",
                }
              : {
                  left: "50%",
                  top: "50%",
                  transform: "translate(-50%, -50%)",
                }),
            backgroundColor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: "50%",
            padding: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "grab",
            zIndex: 11,
          }}
        >
          <SvgColor
            sx={{
              width: "16px",
              height: "16px",
              fontColor: "text.disabled",
              transform: isHorizontal ? "rotate(90deg)" : "rotate(0deg)",
            }}
            src={iconName}
          />
        </div>
      )}
    </div>
  );
};

Divider.propTypes = {
  id: PropTypes.string,
  dividerMargin: PropTypes.string,
  orientation: PropTypes.oneOf(["horizontal", "vertical"]),
  iconPosition: PropTypes.number,
  icon: PropTypes.string,
  showIcon: PropTypes.bool,
  isDragging: PropTypes.bool,
};

const ResizablePanels = ({
  leftPanel,
  rightPanel,
  initialLeftWidth = 50,
  minLeftWidth = 20,
  maxLeftWidth = 80,
  orientation = "horizontal",
  iconPosition = 30,
  icon = null,
  showIcon = false,
}) => {
  const [leftWidth, setLeftWidth] = useState(initialLeftWidth);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef(null);
  const initialPositionRef = useRef(null);
  const initialWidthRef = useRef(null);

  useEffect(() => {
    setLeftWidth(initialLeftWidth);
  }, [initialLeftWidth]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 0,
      },
    }),
  );

  const handleDragStart = () => {
    setIsDragging(true);
    if (containerRef.current) {
      initialPositionRef.current = null;
      initialWidthRef.current =
        orientation === "horizontal"
          ? containerRef.current.getBoundingClientRect().width
          : containerRef.current.getBoundingClientRect().height;
    }
  };

  const handleDragMove = (event) => {
    if (!initialPositionRef.current) {
      initialPositionRef.current =
        orientation === "horizontal" ? event.delta.x : event.delta.y;
      return;
    }

    if (initialWidthRef.current) {
      const delta =
        orientation === "horizontal" ? event.delta.x : event.delta.y;
      const deltaFromInitial = delta - initialPositionRef.current;
      const containerSize = initialWidthRef.current;
      const newLeftWidth = leftWidth + (deltaFromInitial / containerSize) * 100;

      const constrainedWidth = Math.min(
        Math.max(newLeftWidth, minLeftWidth),
        maxLeftWidth,
      );

      setLeftWidth(constrainedWidth);
      initialPositionRef.current = delta;
    }
  };

  const isHorizontal = orientation === "horizontal";

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={() => setIsDragging(false)}
      onDragCancel={() => setIsDragging(false)}
    >
      <div
        ref={containerRef}
        className="panels-container"
        style={{
          display: "flex",
          flexDirection: isHorizontal ? "row" : "column",
          position: "relative",
          width: "100%",
          height: "100%",
          overflow: "hidden",
        }}
      >
        <div
          className="first-panel"
          style={{
            width: isHorizontal ? `${leftWidth}%` : "100%",
            height: isHorizontal ? "100%" : `${leftWidth}%`,
            overflow: "auto",
            boxSizing: "border-box",
            position: "relative",
          }}
        >
          {leftPanel}
        </div>

        <Divider
          id="panel-divider"
          orientation={orientation}
          iconPosition={iconPosition}
          icon={icon}
          showIcon={showIcon}
          isDragging={isDragging}
        />

        <div
          className="second-panel"
          style={{
            width: isHorizontal ? `${100 - leftWidth}%` : "100%",
            height: isHorizontal ? "100%" : `${100 - leftWidth}%`,
            overflow: "auto",
            boxSizing: "border-box",
            position: "relative",
          }}
        >
          {rightPanel}
        </div>
      </div>
    </DndContext>
  );
};

export default ResizablePanels;

ResizablePanels.propTypes = {
  leftPanel: PropTypes.element,
  rightPanel: PropTypes.element,
  initialLeftWidth: PropTypes.number,
  minLeftWidth: PropTypes.number,
  maxLeftWidth: PropTypes.number,
  orientation: PropTypes.oneOf(["horizontal", "vertical"]),
  iconPosition: PropTypes.number,
  icon: PropTypes.string,
  showIcon: PropTypes.bool,
};
