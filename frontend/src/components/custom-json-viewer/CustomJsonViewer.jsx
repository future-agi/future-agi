import { JsonViewer } from "@textea/json-viewer";
import React, { useState, useMemo, memo } from "react";
import PropTypes from "prop-types";
import Image from "src/components/image";
import { defineDataType } from "@textea/json-viewer";
import { Dialog, DialogContent, useTheme } from "@mui/material";
import { jsonToDisplayString } from "src/utils/utils";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "../FormSearchField/FormSearchField";
import { InlineAudio } from "src/components/inline-audio/inline-row-audio";
import { sanitizeSrc, isImageValue, isAudioValue } from "./media-utils";
import _ from "lodash";

const MiniImageRender = (props) => {
  const [open, setOpen] = useState(false);
  const safeSrc = sanitizeSrc(props.value);

  if (!safeSrc) return <span>{props.value}</span>;

  return (
    <>
      <Image
        width={150}
        src={safeSrc}
        alt={safeSrc}
        style={{ display: "inline-block" }}
        onClick={() => setOpen(true)}
      />
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="lg">
        <DialogContent
          sx={{
            bgcolor: "background.paper",
          }}
        >
          <img src={safeSrc} alt="full" style={{ width: "100%" }} />
        </DialogContent>
      </Dialog>
    </>
  );
};

MiniImageRender.propTypes = {
  value: PropTypes.string,
};

const MiniAudioRender = (props) => {
  const safeSrc = sanitizeSrc(props.value);

  if (!safeSrc) return <span>{props.value}</span>;

  return <InlineAudio src={safeSrc} />;
};

MiniAudioRender.propTypes = {
  value: PropTypes.string,
};

const imageType = defineDataType({
  is: (value) => isImageValue(value) && !!sanitizeSrc(value),
  Component: MiniImageRender,
});

const audioType = defineDataType({
  is: (value) => isAudioValue(value) && !!sanitizeSrc(value),
  Component: MiniAudioRender,
});

// Custom comparison function using lodash isEqual
const arePropsEqual = (prevProps, nextProps) => {
  return (
    _.isEqual(prevProps.val, nextProps.val) &&
    prevProps.theme === nextProps.theme
  );
};

export const RenderJSONString = memo(({ val, theme }) => {
  const style = {
    paddingInline: "12px",
    border: "1px solid",
    borderColor: "var(--border-light)",
    backgroundColor:
      theme?.palette?.mode === "dark"
        ? "var(--bg-neutral)"
        : "var(--bg-subtle)",
    textOverflow: "ellipsis",
    overflow: "hidden",
    fontSize: "14px",
    lineHeight: "22px",
    whiteSpace: "nowrap",
    color: "var(--text-primary)",
  };
  return (
    <div className="json-display-string" style={style}>
      {jsonToDisplayString(val)}
    </div>
  );
}, arePropsEqual);

RenderJSONString.displayName = "RenderJSONString";

RenderJSONString.propTypes = {
  val: PropTypes.any, // Changed from PropTypes.string since it accepts objects
  theme: PropTypes.object,
};

const filterJson = (value, term) => {
  if (value == null) return undefined;

  if (Array.isArray(value)) {
    const filtered = value
      .map((item) => filterJson(item, term))
      .filter((item) => item !== undefined);
    return filtered.length > 0 ? filtered : undefined;
  }

  if (typeof value === "object" && !(value instanceof Date)) {
    try {
      const entries = Object.entries(value);
      const filtered = {};
      let hasMatch = false;
      for (const [key, val] of entries) {
        if (key.toLowerCase().includes(term)) {
          filtered[key] = val;
          hasMatch = true;
        } else {
          const result = filterJson(val, term);
          if (result !== undefined) {
            filtered[key] = result;
            hasMatch = true;
          }
        }
      }
      return hasMatch ? filtered : undefined;
    } catch {
      return undefined;
    }
  }

  return String(value).toLowerCase().includes(term) ? value : undefined;
};

const CustomJsonViewer = ({
  object,
  searchable = false,
  searchPlaceholder = "Search",
  ...rest
}) => {
  const theme = useTheme();
  const [searchTerm, setSearchTerm] = useState("");
  const debouncedSearchTerm = useDebounce(searchTerm, 300);

  const handleSearchChange = (e) => {
    setSearchTerm(e?.target?.value ?? "");
  };

  const filteredData = useMemo(() => {
    if (!searchable || !object) return object ?? {};
    const trimmed = debouncedSearchTerm.trim().toLowerCase();
    // Strip surrounding quotes (user may copy values from JSON viewer with quotes)
    const term = trimmed.replace(/^["']|["']$/g, "");
    if (!term) return object;
    try {
      const result = filterJson(object, term);
      return result !== undefined ? result : {};
    } catch {
      return object;
    }
  }, [object, debouncedSearchTerm, searchable]);

  const jsonViewer = (
    <JsonViewer
      value={(searchable ? filteredData : object) ?? {}}
      theme={theme.palette.mode}
      displayDataTypes={false}
      displaySize={false}
      indentWidth={3}
      rootName={false}
      groupArraysAfterLength={500}
      collapseStringsAfterLength={1000}
      highlightUpdates={false}
      editable={false}
      valueTypes={[imageType, audioType]}
      // this set same as Typography s1 as it is used for normal text
      sx={{ fontSize: "14px", lineHeight: "22px" }}
      {...rest}
    />
  );

  if (!searchable) return jsonViewer;

  return (
    <>
      <FormSearchField
        searchQuery={searchTerm}
        onChange={handleSearchChange}
        placeholder={searchPlaceholder}
        fullWidth
        sx={{
          mb: 1.5,
          position: "sticky",
          top: 0,
          zIndex: 1,
          backgroundColor: "background.paper",
        }}
      />
      {jsonViewer}
    </>
  );
};

CustomJsonViewer.propTypes = {
  object: PropTypes.object,
  searchable: PropTypes.bool,
  searchPlaceholder: PropTypes.string,
};

export default CustomJsonViewer;
