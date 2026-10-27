/* eslint-disable react/prop-types */
import { useVirtualizer } from "@tanstack/react-virtual";
import React, { forwardRef, useRef } from "react";

// Virtualised listbox for MUI Autocomplete. Drop-in via
// `ListboxComponent={VirtualizedListbox}`. Renders only the options
// visible in the 260px scroll viewport, keeping frame times flat even
// with 10k+ options.
//
// Below VIRTUALIZE_THRESHOLD we fall back to a plain <ul> so the
// dozens-of-options case doesn't pay for a virtualizer it doesn't need.

const ROW_HEIGHT = 32;
const MAX_HEIGHT = 260;
const OVERSCAN = 10;
const VIRTUALIZE_THRESHOLD = 50;

const VirtualizedListContent = forwardRef(
  function VirtualizedListContent(props, ref) {
    const { items, style, ...rest } = props;
    const parentRef = useRef(null);

    const setRef = (node) => {
      parentRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    };

    const virtualizer = useVirtualizer({
      count: items.length,
      getScrollElement: () => parentRef.current,
      estimateSize: () => ROW_HEIGHT,
      overscan: OVERSCAN,
    });

    return (
      <ul
        ref={setRef}
        {...rest}
        style={{
          ...style,
          maxHeight: MAX_HEIGHT,
          overflowY: "auto",
          position: "relative",
          margin: 0,
          padding: 0,
        }}
      >
        <li
          style={{
            position: "relative",
            height: virtualizer.getTotalSize(),
            listStyle: "none",
            padding: 0,
            margin: 0,
          }}
        >
          {virtualizer.getVirtualItems().map((row) => {
            const item = items[row.index];
            if (!item) return null;
            return React.cloneElement(item, {
              key: row.key,
              style: {
                ...(item.props?.style || {}),
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${row.start}px)`,
                height: row.size,
              },
            });
          })}
        </li>
      </ul>
    );
  },
);

const VirtualizedListbox = forwardRef(function VirtualizedListbox(props, ref) {
  const { children, ...rest } = props;
  const items = React.Children.toArray(children);

  if (items.length < VIRTUALIZE_THRESHOLD) {
    return (
      <ul ref={ref} {...rest}>
        {children}
      </ul>
    );
  }

  return <VirtualizedListContent ref={ref} items={items} {...rest} />;
});

export default VirtualizedListbox;
