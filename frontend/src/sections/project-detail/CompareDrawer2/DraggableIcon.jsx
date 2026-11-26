import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Icon } from "@iconify/react";

const DraggableIcon = ({ top, setTop, minTop, maxTop, offset = 130 }) => {
  const iconRef = useRef(null);
  const isDragging = useRef(false);
  const dragOffset = useRef(0);

  const handleMouseDown = (e) => {
    const parentRect = iconRef.current.parentNode.getBoundingClientRect();
    const cursorRelParent = e.clientY - parentRect.top;
    dragOffset.current = cursorRelParent - top;
    isDragging.current = true;
    e.preventDefault();
  };

  const handleMouseMove = (e) => {
    if (!isDragging.current) {
      return;
    }
    const parentRect = iconRef.current.parentNode.getBoundingClientRect();
    const cursorRelParent = e.clientY - parentRect.top;

    let newTop = cursorRelParent - dragOffset.current;
    newTop = Math.max(minTop, Math.min(maxTop, newTop));
    setTop(newTop);
  };

  const handleMouseUp = () => {
    isDragging.current = false;
  };

  useEffect(() => {
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  });

  return (
    <Icon
      icon="solar:grip-horizontal-linear"
      ref={iconRef}
      onMouseDown={handleMouseDown}
      style={{
        position: "absolute",
        top: `${top + offset}px`,
        left: 20,
        background: "var(--bg-paper)",
        borderRadius: "50%",
        padding: 4,
        color: "grey",
        zIndex: 1,
        border: "1px solid var(--border-default)",
        cursor: "grab",
        userSelect: "none",
      }}
      width={22}
      height={22}
    />
  );
};

DraggableIcon.propTypes = {
  top: PropTypes.number.isRequired,
  setTop: PropTypes.func.isRequired,
  minTop: PropTypes.number.isRequired,
  maxTop: PropTypes.number.isRequired,
  offset: PropTypes.number,
};

export default DraggableIcon;
