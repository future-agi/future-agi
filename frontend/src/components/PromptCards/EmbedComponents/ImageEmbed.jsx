import React from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { formatFileSize } from "src/utils/utils";

// This component is embedded inside quill editor via createRoot and does NOT have
// access to MUI ThemeProvider. All styling must use CSS variables or inline styles.
const ImageEmbed = ({
  url,
  name,
  size,
  onDelete,
  onReplace,
  onMagnify,
  isEmbed,
  id,
}) => {
  const iconButtonStyle = {
    background: "none",
    border: "none",
    padding: 0,
    cursor: "pointer",
    display: "flex",
  };

  return (
    <div
      style={{
        border: "1px solid var(--border-default)",
        borderRadius: "8px",
        padding: "12px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        backgroundColor: "var(--bg-paper)",
        marginTop: isEmbed ? "8px" : "0px",
        marginBottom: isEmbed ? "8px" : "0px",
      }}
      id={id}
    >
      <div style={{ display: "flex", gap: "8px", flex: 1, overflow: "hidden" }}>
        <div
          style={{
            height: "42px",
            width: "42px",
            overflow: "hidden",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            borderRadius: "2px",
            flexShrink: 0,
          }}
        >
          <img
            src={url}
            alt=""
            style={{ maxWidth: "100%", maxHeight: "100%" }}
          />
        </div>
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
            {name?.length > 0 ? name : "Image"}
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
        <ShowComponent condition={Boolean(onMagnify)}>
          <button type="button" onClick={onMagnify} style={iconButtonStyle}>
            <img
              src="/assets/icons/components/ic_magnify.svg"
              alt="magnify"
              width={16}
              height={16}
              style={{ filter: "var(--icon-filter, none)" }}
            />
          </button>
        </ShowComponent>
        <ShowComponent condition={Boolean(onReplace)}>
          <button type="button" onClick={onReplace} style={iconButtonStyle}>
            <img
              src="/assets/icons/components/ic_replace.svg"
              alt="replace"
              width={16}
              height={16}
              style={{ filter: "var(--icon-filter, none)" }}
            />
          </button>
        </ShowComponent>
        <ShowComponent condition={Boolean(onDelete)}>
          <button type="button" onClick={onDelete} style={iconButtonStyle}>
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
  );
};

ImageEmbed.propTypes = {
  url: PropTypes.string.isRequired,
  name: PropTypes.string,
  size: PropTypes.number,
  onDelete: PropTypes.func,
  onReplace: PropTypes.func,
  onMagnify: PropTypes.func,
  isEmbed: PropTypes.bool,
  id: PropTypes.string,
};

export default ImageEmbed;
