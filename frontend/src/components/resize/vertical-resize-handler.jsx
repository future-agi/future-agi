import React from "react";
import { PanelResizeHandle } from "react-resizable-panels";

import PropTypes from "prop-types";
import styles from "./styles.module.css";

export default function VerticalResizeHandle({ className = "", id }) {
  return (
    <PanelResizeHandle
      className={[styles.ResizeHandleOuter, className].join(" ")}
      id={id}
    >
      <div className={styles.ResizeHandleInner}>
        {/* <Iconify icon="solar:separator-vertical-linear" /> */}
      </div>
    </PanelResizeHandle>
  );
}

VerticalResizeHandle.propTypes = {
  className: PropTypes.string,
  id: PropTypes.string,
};
