import React from "react";
import { ShowComponent } from "../../show";
import { formatFileSize } from "src/utils/utils";
import PropTypes from "prop-types";

// This component is embedded inside quill editor via createRoot and does NOT have
// access to MUI ThemeProvider. All styling must use CSS variables or inline styles.
const PdfEmbed = ({ isEmbed, id, size, onDelete, name }) => {
  return (
    <div
      style={{
        border: "1px solid var(--border-default)",
        borderRadius: "8px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        backgroundColor: "var(--bg-paper)",
        marginTop: isEmbed ? "8px" : "0px",
        marginBottom: isEmbed ? "8px" : "0px",
      }}
      id={id}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <div
          style={{ display: "flex", gap: "8px", flex: 1, overflow: "hidden" }}
        >
          <img src="/icons/fileIcons/pdf.svg" alt="" width={22} />
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              overflow: "hidden",
            }}
          >
            <span
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 500,
                fontSize: "14px",
                fontFamily: "IBM Plex Sans,sans-serif",
                color: "var(--text-primary)",
              }}
            >
              {name?.length > 0 ? name : "PDF"}
            </span>
            <ShowComponent condition={size !== undefined}>
              <span
                style={{
                  fontSize: "12px",
                  fontFamily: "IBM Plex Sans,sans-serif",
                  color: "var(--text-disabled)",
                }}
              >
                {formatFileSize(size)}
              </span>
            </ShowComponent>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <ShowComponent condition={Boolean(onDelete)}>
            <button
              type="button"
              onClick={onDelete}
              style={{
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                display: "flex",
              }}
            >
              <img
                src="/assets/icons/ic_delete.svg"
                alt="delete"
                width={16}
                height={16}
                style={{ filter: "var(--icon-filter, none)" }}
              />
            </button>
          </ShowComponent>
        </div>
      </div>
    </div>
  );
};

PdfEmbed.propTypes = {
  isEmbed: PropTypes.bool,
  id: PropTypes.string,
  size: PropTypes.number,
  onDelete: PropTypes.func,
  name: PropTypes.string,
};

export default PdfEmbed;
