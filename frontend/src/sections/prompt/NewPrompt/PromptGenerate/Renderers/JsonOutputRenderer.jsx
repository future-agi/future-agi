import { Box, IconButton, Tooltip, Typography, alpha } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";

const JsonOutputRenderer = ({
  data,
  columnName = "output",
  maxDepth = 5,
  onPathCopy,
  showPaths = true,
  initialExpanded = true,
  showRawOnInvalid = false,
}) => {
  const [expandedPaths, setExpandedPaths] = useState(() => {
    const initial = new Set();
    if (initialExpanded) {
      initial.add("");
    }
    return initial;
  });

  const parsedData = useMemo(() => {
    if (typeof data === "string") {
      try {
        return JSON.parse(data);
      } catch {
        return null;
      }
    }
    return data;
  }, [data]);

  const toggleExpand = useCallback((path) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    const paths = new Set([""]);
    const collectPaths = (obj, currentPath) => {
      if (typeof obj === "object" && obj !== null) {
        Object.keys(obj).forEach((key) => {
          const newPath = Array.isArray(obj)
            ? `${currentPath}[${key}]`
            : currentPath
              ? `${currentPath}.${key}`
              : key;
          paths.add(newPath);
          collectPaths(obj[key], newPath);
        });
      }
    };
    collectPaths(parsedData, "");
    setExpandedPaths(paths);
  }, [parsedData]);

  const collapseAll = useCallback(() => {
    setExpandedPaths(new Set([""]));
  }, []);

  const handleCopyPath = useCallback(
    (path) => {
      const fullPath = `{{${columnName}.${path}}}`;
      copyToClipboard(fullPath);
      enqueueSnackbar(`Copied: ${fullPath}`, { variant: "success" });
      onPathCopy?.(fullPath);
    },
    [columnName, onPathCopy],
  );

  const handleCopyValue = useCallback((value) => {
    const stringValue =
      typeof value === "object"
        ? JSON.stringify(value, null, 2)
        : String(value);
    copyToClipboard(stringValue);
    enqueueSnackbar("Value copied to clipboard", { variant: "success" });
  }, []);

  const renderValue = useCallback(
    (value, path, depth) => {
      if (depth > maxDepth) {
        return (
          <Typography
            component="span"
            sx={{
              color: "text.disabled",
              fontStyle: "italic",
              fontSize: "13px",
            }}
          >
            {"<max depth reached>"}
          </Typography>
        );
      }

      if (value === null) {
        return (
          <Typography
            component="span"
            sx={{ color: "error.main", fontSize: "13px" }}
          >
            null
          </Typography>
        );
      }

      if (typeof value === "boolean") {
        return (
          <Typography
            component="span"
            sx={{ color: "warning.main", fontSize: "13px" }}
          >
            {value.toString()}
          </Typography>
        );
      }

      if (typeof value === "number") {
        return (
          <Typography
            component="span"
            sx={{ color: "info.main", fontSize: "13px" }}
          >
            {value}
          </Typography>
        );
      }

      if (typeof value === "string") {
        const displayValue =
          value.length > 100 ? `${value.substring(0, 100)}...` : value;
        return (
          <Typography
            component="span"
            sx={{ color: "success.dark", fontSize: "13px" }}
          >
            &quot;{displayValue}&quot;
          </Typography>
        );
      }

      if (Array.isArray(value)) {
        const isExpanded = expandedPaths.has(path);
        return (
          <Box component="span">
            <IconButton
              size="small"
              onClick={() => toggleExpand(path)}
              sx={{ p: 0, mr: 0.5 }}
            >
              <Iconify
                icon={isExpanded ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            </IconButton>
            <Typography
              component="span"
              sx={{ color: "text.secondary", fontSize: "13px" }}
            >
              Array[{value.length}]
            </Typography>
            {isExpanded && (
              <Box
                sx={{ pl: 2, borderLeft: "1px solid", borderColor: "divider" }}
              >
                {value.map((item, index) => (
                  <Box
                    key={index}
                    sx={{ display: "flex", alignItems: "flex-start", py: 0.5 }}
                  >
                    <JsonKeyValue
                      keyName={`[${index}]`}
                      value={item}
                      path={`${path}[${index}]`}
                      depth={depth + 1}
                      showPaths={showPaths}
                      columnName={columnName}
                      onCopyPath={handleCopyPath}
                      onCopyValue={handleCopyValue}
                      expandedPaths={expandedPaths}
                      toggleExpand={toggleExpand}
                      maxDepth={maxDepth}
                      renderValue={renderValue}
                    />
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        );
      }

      if (typeof value === "object") {
        const keys = Object.keys(value);
        const isExpanded = expandedPaths.has(path);
        return (
          <Box component="span">
            <IconButton
              size="small"
              onClick={() => toggleExpand(path)}
              sx={{ p: 0, mr: 0.5 }}
            >
              <Iconify
                icon={isExpanded ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            </IconButton>
            <Typography
              component="span"
              sx={{ color: "text.secondary", fontSize: "13px" }}
            >
              {"{"}
              {keys.length} keys
              {"}"}
            </Typography>
            {isExpanded && (
              <Box
                sx={{ pl: 2, borderLeft: "1px solid", borderColor: "divider" }}
              >
                {keys.map((key) => (
                  <Box
                    key={key}
                    sx={{ display: "flex", alignItems: "flex-start", py: 0.5 }}
                  >
                    <JsonKeyValue
                      keyName={key}
                      value={value[key]}
                      path={path ? `${path}.${key}` : key}
                      depth={depth + 1}
                      showPaths={showPaths}
                      columnName={columnName}
                      onCopyPath={handleCopyPath}
                      onCopyValue={handleCopyValue}
                      expandedPaths={expandedPaths}
                      toggleExpand={toggleExpand}
                      maxDepth={maxDepth}
                      renderValue={renderValue}
                    />
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        );
      }

      return (
        <Typography component="span" sx={{ fontSize: "13px" }}>
          {String(value)}
        </Typography>
      );
    },
    [
      expandedPaths,
      toggleExpand,
      maxDepth,
      showPaths,
      columnName,
      handleCopyPath,
      handleCopyValue,
    ],
  );

  if (parsedData === null) {
    if (showRawOnInvalid) {
      return (
        <Typography
          component="pre"
          sx={{
            overflow: "hidden",
            wordWrap: "break-word",
            whiteSpace: "pre-wrap",
          }}
          typography={"m3"}
          color="text.primary"
          fontWeight="fontWeightRegular"
        >
          {data}
        </Typography>
      );
    }
    return (
      <Typography color="error" fontSize="13px">
        Invalid JSON
      </Typography>
    );
  }

  return (
    <Box sx={{ fontFamily: "monospace", fontSize: "13px" }}>
      <Box sx={{ display: "flex", gap: 1, mb: 1 }}>
        <Tooltip title="Expand all">
          <IconButton size="small" onClick={expandAll}>
            <Iconify icon="mdi:unfold-more-horizontal" width={18} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Collapse all">
          <IconButton size="small" onClick={collapseAll}>
            <Iconify icon="mdi:unfold-less-horizontal" width={18} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Copy all">
          <IconButton size="small" onClick={() => handleCopyValue(parsedData)}>
            <Iconify icon="basil:copy-outline" width={18} />
          </IconButton>
        </Tooltip>
      </Box>
      {renderValue(parsedData, "", 0)}
    </Box>
  );
};

const JsonKeyValue = ({
  keyName,
  value,
  path,
  depth,
  showPaths,
  columnName,
  onCopyPath,
  onCopyValue,
  expandedPaths: _expandedPaths,
  toggleExpand: _toggleExpand,
  maxDepth: _maxDepth,
  renderValue,
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const isLeaf = typeof value !== "object" || value === null;
  const fullVariablePath = `{{${columnName}.${path}}}`;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        flex: 1,
        position: "relative",
        backgroundColor: isHovered
          ? (theme) => alpha(theme.palette.primary.main, 0.05)
          : "transparent",
        borderRadius: 0.5,
        px: 0.5,
        "&:hover .copy-buttons": {
          opacity: 1,
        },
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <Typography
        component="span"
        sx={{
          color: "primary.main",
          fontWeight: 500,
          mr: 1,
          fontSize: "13px",
        }}
      >
        {keyName}:
      </Typography>
      <Box sx={{ flex: 1 }}>{renderValue(value, path, depth)}</Box>
      {showPaths && isLeaf && (
        <Box
          className="copy-buttons"
          sx={{
            display: "flex",
            gap: 0.5,
            opacity: 0,
            transition: "opacity 0.2s",
            ml: 1,
          }}
        >
          <Tooltip title={`Copy path: ${fullVariablePath}`}>
            <IconButton
              size="small"
              onClick={() => onCopyPath(path)}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="mdi:code-braces" width={14} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Copy value">
            <IconButton
              size="small"
              onClick={() => onCopyValue(value)}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="basil:copy-outline" width={14} />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </Box>
  );
};

JsonKeyValue.propTypes = {
  keyName: PropTypes.string.isRequired,
  value: PropTypes.any,
  path: PropTypes.string.isRequired,
  depth: PropTypes.number.isRequired,
  showPaths: PropTypes.bool,
  columnName: PropTypes.string,
  onCopyPath: PropTypes.func,
  onCopyValue: PropTypes.func,
  expandedPaths: PropTypes.instanceOf(Set),
  toggleExpand: PropTypes.func,
  maxDepth: PropTypes.number,
  renderValue: PropTypes.func,
};

JsonOutputRenderer.propTypes = {
  data: PropTypes.any.isRequired,
  columnName: PropTypes.string,
  maxDepth: PropTypes.number,
  onPathCopy: PropTypes.func,
  showPaths: PropTypes.bool,
  initialExpanded: PropTypes.bool,
  showRawOnInvalid: PropTypes.bool,
};

export default JsonOutputRenderer;
