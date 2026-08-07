import { useState, useCallback, useRef, useEffect } from "react";

// ----------------------------------------------------------------------

export function useRowHover(props) {
  const [isRowHovered, setIsRowHovered] = useState(false);
  const cellRef = useRef(null);

  const handleMouseEnter = useCallback(() => setIsRowHovered(true), []);
  const handleMouseLeave = useCallback(() => setIsRowHovered(false), []);

  useEffect(() => {
    if (!props.node) return;

    let rowElement = null;
    let cleanup = () => {};

    const setupAgGridEvents = () => {
      if (props.node.addEventListener) {
        props.node.addEventListener("mouseEnter", handleMouseEnter);
        props.node.addEventListener("mouseLeave", handleMouseLeave);
        return () => {
          props.node.removeEventListener("mouseEnter", handleMouseEnter);
          props.node.removeEventListener("mouseLeave", handleMouseLeave);
        };
      }
      return () => {};
    };

    const setupDomEvents = () => {
      if (!cellRef.current) return () => {};

      const findRowElement = () => {
        let element = cellRef.current;
        while (element && !element.classList.contains("ag-row")) {
          element = element.parentElement;
        }
        return element;
      };

      rowElement = findRowElement();
      if (rowElement) {
        rowElement.addEventListener("mouseenter", handleMouseEnter);
        rowElement.addEventListener("mouseleave", handleMouseLeave);
        return () => {
          rowElement.removeEventListener("mouseenter", handleMouseEnter);
          rowElement.removeEventListener("mouseleave", handleMouseLeave);
        };
      }
      return () => {};
    };

    const agGridCleanup = setupAgGridEvents();
    const domCleanup = setupDomEvents();

    cleanup = () => {
      agGridCleanup();
      domCleanup();
    };

    return cleanup;
  }, [props.node, handleMouseEnter, handleMouseLeave]);

  return { isRowHovered, cellRef };
}
