import React from "react";
import { getFileIcon } from "src/sections/knowledge-base/sheet-view/icons";
import { getFileExtension } from "src/utils/utils";

const RenderFileIcons = (fileType) => {
  try {
    const iconType = getFileExtension(fileType) || "default";
    const iconPath = getFileIcon(iconType);

    return (
      <img
        src={iconPath}
        style={{
          minWidth: 22,
          minHeight: 22,
        }}
        onError={(e) => {
          e.target.onerror = null;
          e.target.src = "/icons/fileIcons/default.svg";
        }}
        alt={`${fileType} file icon`}
      />
    );
  } catch (error) {
    return (
      <img
        src="/icons/fileIcons/default.svg"
        style={{
          minWidth: 22,
          minHeight: 22,
        }}
        alt="Default file icon"
      />
    );
  }
};

export default RenderFileIcons;
