import React from "react";
import CustomAudioPlayer from "../../custom-audio/CustomAudioPlayer";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { formatFileSize } from "src/utils/utils";
import { fileIconByMimeType } from "../../../utils/constants";

// This component is embedded inside quill editor via createRoot and does NOT have
// access to MUI ThemeProvider. All styling must use CSS variables or inline styles.
const AudioEmbed = ({ isEmbed, id, name, size, onDelete, url, mimeType }) => {
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
        whiteSpaceCollapse: "collapse",
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
          <ShowComponent condition={mimeType}>
            <img src={fileIconByMimeType[mimeType]} alt="" width={22} />
          </ShowComponent>
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
              {name?.length > 0 ? name : "Audio"}
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

      <CustomAudioPlayer
        audioData={{
          url: url,
        }}
      />
    </div>
  );
};

AudioEmbed.propTypes = {
  isEmbed: PropTypes.bool,
  id: PropTypes.string,
  name: PropTypes.string,
  size: PropTypes.number,
  onDelete: PropTypes.func,
  url: PropTypes.string,
  mimeType: PropTypes.string,
};

export default AudioEmbed;
