import React, { useEffect, useState, useRef } from "react";
import { TreeView, treeItemClasses } from "@mui/x-tree-view";
import { TreeItem } from "@mui/x-tree-view";
import {
  Typography,
  Box,
  styled,
  Popper,
  Paper,
  Checkbox,
  FormControlLabel,
  ClickAwayListener,
} from "@mui/material";
import Iconify from "../iconify";
import { useSelectedNode } from "./useSelectedNode";
import PropTypes from "prop-types";
import _ from "lodash";
import SvgColor from "../svg-color";
import Tooltip from "@mui/material/Tooltip";
import Image from "src/components/image";
import { getObservationSpanById, getTraceIconLabel } from "./common";
import CustomTooltip from "../tooltip";

const CustomTreeItem = styled(TreeItem)(({ theme }) => ({
  position: "relative",
  paddingLeft: 0,

  [`& .${treeItemClasses.content}`]: {
    padding: theme.spacing(0.5, 1),
    margin: theme.spacing(0.2, 0),
    borderRadius: "4px",
    paddingLeft: 0,
    "&:hover": {
      backgroundColor: "background.neutral !important",
    },
    "&.Mui-selected": {
      backgroundColor: "background.neutral !important",
    },
    "&.Mui-selected:hover": {
      backgroundColor: "background.neutral !important",
    },
  },

  [`& .${treeItemClasses.group}`]: {
    marginLeft: 24,
    paddingLeft: 24,
    position: "relative",

    "&::before": {
      content: "none",
    },
  },

  "& .MuiTreeItem-root": {
    position: "relative",

    "&::before": {
      content: '""',
      position: "absolute",
      left: 25.5,
      width: 21,
      height: 33,
      top: "-14.2px",
      borderBottom: "2px solid",
      borderBottomColor: theme.palette.divider,
      borderLeft: "2px solid",
      borderLeftColor: theme.palette.divider,
      borderTop: "none",
      borderRight: "none",
      zIndex: 1,
    },
  },

  "& .MuiTreeItem-root:not(:last-child)::after": {
    content: '""',
    position: "absolute",
    left: 25.5,
    top: 15,
    height: "100%",
    width: 2,
    backgroundColor: theme.palette.divider,
    zIndex: 0,
  },

  [`& .${treeItemClasses.iconContainer}`]: {
    display: "none",
  },
}));

const TreeNode = ({
  node,
  children,
  columnOptions,
  expanded,
  setExpanded,
  handleNodeSelect,
  depth = 0,
  selectedNodeId,
  disableOnClick = false,
}) => {
  const isExpanded = expanded.includes(node.id);
  const hasChildren = Array.isArray(children) && children.length > 0;
  const isStandaloneNode = !hasChildren && depth === 0;

  return (
    <CustomTreeItem
      key={node.id}
      nodeId={node.id}
      sx={{
        [`& .${treeItemClasses.group}`]: {
          "&::before": {
            backgroundColor: !children?.length ? "transparent" : "",
          },
        },
        marginLeft: "-20px",
      }}
      label={
        <div
          style={{
            display: "flex",
            alignItems: "center",
            width: "100%",
          }}
          onClick={(event) => {
            event.stopPropagation();
            if (disableOnClick) return;
            handleNodeSelect(event, node.id);
          }}
        >
          <Box
            sx={{ mr: 1 }}
            className="iconContainer"
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((prevExpanded) =>
                prevExpanded.includes(node.id)
                  ? prevExpanded.filter((id) => id !== node.id)
                  : [...prevExpanded, node.id],
              );
            }}
          >
            {hasChildren && (
              <Iconify
                // @ts-ignore
                icon="line-md:chevron-right"
                color="text.secondary"
                width={18}
                sx={{
                  position: "absolute",
                  zIndex: 1,
                  left: `${-depth * 28 + 18}px`,
                  top: 4,
                  transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
                  transition: "transform 0.3s ease",
                }}
              />
            )}
          </Box>

          {/* Main container with ref for measurement */}
          <Box
            sx={{
              display: "flex",
              position: "relative",
              width: "100%",
            }}
          >
            {/* Left part with dynamically calculated max-width */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                marginLeft: isStandaloneNode ? 15 : hasChildren ? 30 : 34,
                overflow: "hidden",
              }}
            >
              <CustomTooltip
                show={
                  node?.observation_type && node?.observation_type !== "unknown"
                }
                title={node?.observation_type}
                placement="bottom"
                arrow
                size="small"
                type="black"
                slotProps={{
                  tooltip: {
                    sx: {
                      maxWidth: "200px !important",
                    },
                  },
                  popper: {
                    modifiers: [
                      {
                        name: "preventOverflow",
                        options: {
                          boundary: "viewport",
                          padding: 12,
                        },
                      },
                    ],
                  },
                }}
              >
                <Image
                  // @ts-ignore
                  src={getTraceIconLabel(node?.observation_type)}
                  width={18}
                  style={{ marginBottom: 7, flexShrink: 0 }}
                />
              </CustomTooltip>
              <Typography
                typography="s2"
                noWrap
                sx={{
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "text.primary",
                  marginLeft: "8px",
                  overflow: "visible",
                }}
              >
                {node.name}
              </Typography>
              <Typography
                typography="s2"
                noWrap
                sx={{ overflow: "visible", minWidth: "200px" }}
              />
            </div>
          </Box>
        </div>
      }
    >
      {Array.isArray(children)
        ? children.map((childNode) => (
            <TreeNode
              key={childNode.observation_span.id}
              node={childNode.observation_span}
              children={childNode.children}
              columnOptions={columnOptions}
              expanded={expanded}
              setExpanded={setExpanded}
              handleNodeSelect={handleNodeSelect}
              depth={depth + 1}
              selectedNodeId={selectedNodeId}
            />
          ))
        : null}
    </CustomTreeItem>
  );
};

// PropTypes for TreeNode
TreeNode.propTypes = {
  node: PropTypes.object.isRequired,
  children: PropTypes.array,
  columnOptions: PropTypes.array.isRequired,
  expanded: PropTypes.array.isRequired,
  setExpanded: PropTypes.func.isRequired,
  handleNodeSelect: PropTypes.func.isRequired,
  depth: PropTypes.number,
  selectedNodeId: PropTypes.string,
  disableOnClick: PropTypes.bool,
};

const getAllObservationSpanIds = (treeData) => {
  const ids = [];

  const traverse = (nodes) => {
    for (const node of nodes) {
      if (node.observation_span?.id) {
        ids.push(node.observation_span.id);
      }
      if (Array.isArray(node.children)) {
        traverse(node.children);
      }
    }
  };

  traverse(treeData);
  return ids;
};

const TraceTree = ({
  treeData,
  label,
  defaultSelectedSpanId,
  columnOptionItems,
  onTraceNodeSelect,
  disableOnClick = false,
}) => {
  const [expanded, setExpanded] = useState([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [columnOptions, setColumnOptions] = useState(columnOptionItems || []);

  const anchorRef = useRef(null);

  const { setSelectedNode, selectedNode } = useSelectedNode();

  useEffect(() => {
    setSelectedNode(null);
    let defaultNode = treeData[0]?.observation_span;

    if (defaultSelectedSpanId) {
      const foundNode = getObservationSpanById(treeData, defaultSelectedSpanId);
      if (foundNode) {
        defaultNode = foundNode;
      }
    }
    if (defaultNode) {
      setSelectedNode(defaultNode);
    }

    setExpanded(getAllObservationSpanIds(treeData));

    return () => {
      setSelectedNode(null);
      setExpanded([]);
    };
  }, [treeData, defaultSelectedSpanId, setSelectedNode]);

  // Update column options when prop changes
  useEffect(() => {
    if (columnOptionItems && Array.isArray(columnOptionItems)) {
      setColumnOptions(columnOptionItems);
    }
  }, [columnOptionItems]);

  const handleNodeSelect = (event, nodeId) => {
    if (selectedNode?.id === nodeId) {
      if (onTraceNodeSelect) {
        onTraceNodeSelect?.();
      }
      return; // Already selected, no need to do anything
    }
    const findNode = (nodes) => {
      for (const { observation_span: observationSpan, children } of nodes) {
        if (observationSpan.id === nodeId) return observationSpan;
        if (children) {
          const found = findNode(children);
          if (found) return found;
        }
      }
      return null;
    };

    const foundNode = findNode(treeData);
    if (foundNode) {
      setSelectedNode(foundNode);
    }

    if (onTraceNodeSelect) {
      onTraceNodeSelect();
    }
  };

  const handleCollapse = () => {
    if (expanded.length > 0) {
      setExpanded([]);
    } else {
      setExpanded(getAllObservationSpanIds(treeData));
    }
  };

  const toggleDropdown = () => {
    setDropdownOpen((prev) => !prev);
  };

  const handleColumnOptionChange = (key) => {
    setColumnOptions((prev) =>
      prev.map((option) =>
        option.key === key ? { ...option, visible: !option.visible } : option,
      ),
    );
  };

  return (
    <Box sx={{ height: "100%" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
          paddingBottom: 1,
          paddingRight: 3,
          paddingLeft: 3,
          alignItems: "center",
        }}
      >
        <Box sx={{ flex: 1 }}>
          <Typography color="text.secondary" fontSize="13px" fontWeight={700}>
            {label}
          </Typography>
        </Box>
        <Box
          onClick={handleCollapse}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            paddingX: "7px",
            paddingY: "4px",
            cursor: "pointer",
          }}
        >
          {/* @ts-ignore */}
          <Iconify icon="bi:arrows-collapse" color="text.primary" width={14} />
        </Box>
        <Box
          ref={anchorRef}
          onClick={toggleDropdown}
          sx={{
            height: "24px",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            paddingX: "7px",
            cursor: "pointer",
            backgroundColor: dropdownOpen ? "action.hover" : "transparent",
          }}
        >
          <Image
            // @ts-ignore
            src="/icons/datasets/trace_column.svg"
            alt="Toggle columns"
            sx={{
              "& .component-image-wrapper": {
                width: "100% !important",
                height: "100% !important",
                verticalAlign: "middle",
              },
              "& img": {
                width: "100%",
                height: "100%",
                objectFit: "contain",
                verticalAlign: "middle",
                filter: (theme) =>
                  theme?.palette?.mode === "dark" ? "invert(1)" : "none",
              },
            }}
          />
        </Box>

        <Popper
          open={dropdownOpen}
          anchorEl={anchorRef.current}
          placement="bottom-end"
          style={{ zIndex: 1300 }}
        >
          <ClickAwayListener onClickAway={() => setDropdownOpen(false)}>
            <Paper
              sx={{
                width: 177,
                mt: 1,
                py: 1,
                boxShadow: "0px 5px 15px rgba(0, 0, 0, 0.15)",
                borderRadius: "8px",
              }}
            >
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                {columnOptions.map(({ key, label, visible }) => (
                  <FormControlLabel
                    key={key}
                    sx={{ mx: 1, my: 0, gap: 1 }}
                    control={
                      <Checkbox
                        checked={visible}
                        onChange={() => handleColumnOptionChange(key)}
                        sx={{
                          padding: "4px",
                        }}
                        size="small"
                      />
                    }
                    label={
                      <Typography
                        sx={{
                          fontSize: "14px",
                          color: "text.primary",
                          fontWeight: 400,
                        }}
                      >
                        {label}
                      </Typography>
                    }
                  />
                ))}
              </Box>
            </Paper>
          </ClickAwayListener>
        </Popper>
      </Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          position: "relative",
          overflow: "auto",
          "&::-webkit-scrollbar": {
            height: "6px",
          },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "rgba(0, 0, 0, 0.3)",
            borderRadius: "3px",
          },
          "&::-webkit-scrollbar-track": {
            backgroundColor: "transparent",
          },
        }}
      >
        <Box>
          <TreeView
            aria-label="trace tree"
            expanded={expanded}
            defaultCollapseIcon=""
            defaultExpandIcon=""
            defaultEndIcon=""
            selected={selectedNode?.id || ""}
            onNodeSelect={handleNodeSelect}
            onNodeToggle={(event, nodeIds) => setExpanded(nodeIds)}
            style={{ marginLeft: "10px" }}
            multiSelect={false}
          >
            {treeData.map((data) => (
              <TreeNode
                key={data.observation_span.id}
                node={data.observation_span}
                children={data.children}
                columnOptions={columnOptions}
                expanded={expanded}
                setExpanded={setExpanded}
                handleNodeSelect={handleNodeSelect}
                depth={0}
                disableOnClick={disableOnClick}
                selectedNodeId={selectedNode?.id || ""}
              />
            ))}
          </TreeView>
        </Box>
        {/* Right part with ref for measurement */}
        <Box
          sx={{
            position: "sticky",
            top: 0,
            bottom: 0,
            right: 0,
            backgroundColor: "background.paper",
            minWidth: "max-content",
            zIndex: 5,
          }}
        >
          {treeData.map((data) => (
            <RightSection
              key={data.observation_span.id}
              node={data.observation_span}
              children={data.children}
              expanded={expanded}
              setExpanded={setExpanded}
              columnOptions={columnOptions}
              handleNodeSelect={handleNodeSelect}
              depth={0}
              selectedNodeId={selectedNode?.id || ""}
            />
          ))}
        </Box>
      </Box>
    </Box>
  );
};

TraceTree.propTypes = {
  treeData: PropTypes.array,
  label: PropTypes.string,
  defaultSelectedSpanId: PropTypes.string,
  columnOptionItems: PropTypes.array,
  onTraceNodeSelect: PropTypes.func,
  disableOnClick: PropTypes.bool,
};

export default TraceTree;

const RightSection = ({
  node,
  children,
  columnOptions,
  expanded,
  setExpanded,
  handleNodeSelect,
  depth = 0,
  selectedNodeId,
}) => {
  const isExpanded = expanded.includes(node.id);
  const hasChildren = Array.isArray(children) && children.length > 0;
  const isStandaloneNode = !hasChildren && depth === 0;

  const isColumnVisible = (key) => {
    const option = columnOptions.find((item) => item.key === key);
    return option && option.visible;
  };

  const visibleRightItems = [
    isColumnVisible("latency") && node?.latency_ms,
    isColumnVisible("tokens") && node?.total_tokens,
    isColumnVisible("evals") && node?.totalEvalsCount,
    isColumnVisible("annotations") && node?.totalAnnotationsCount > 0,
    isColumnVisible("events") && node?.totalEventsCount > 0,
    isColumnVisible("cost") && node?.completion_tokens && node?.prompt_tokens,
  ].filter(Boolean).length;

  return (
    <Box
      display="flex"
      flexDirection={"column"}
      gap={0.5}
      marginTop={isStandaloneNode ? "0px" : "0px"}
    >
      <Box
        sx={{
          display: "flex",
          padding: "3px",
          cursor: "pointer",
          justifyContent: "flex-end",
        }}
        onClick={(event) => {
          event.stopPropagation();
          handleNodeSelect(event, node.id);
        }}
      >
        {!visibleRightItems && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              borderRadius: "6px",
              padding: "7px 6px",
              height: "32px",
            }}
          >
            <Typography typography="s3" />
          </Box>
        )}
        {isColumnVisible("latency") && node?.latency_ms ? (
          <Tooltip title={`Time: ${node?.latency_ms} ms`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <Iconify
                // @ts-ignore
                icon="material-symbols:schedule-outline"
                width="12px"
                height="12px"
                color="text.secondary"
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {node?.latency_ms}ms
              </Typography>
            </Box>
          </Tooltip>
        ) : null}

        {isColumnVisible("tokens") && node?.total_tokens ? (
          <Tooltip title={`Tokens: ${node?.total_tokens}`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/components/ic_coin.svg`}
                sx={{ width: "12px", height: "15px", color: "text.secondary" }}
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {node?.total_tokens}
              </Typography>
            </Box>
          </Tooltip>
        ) : null}

        {isColumnVisible("evals") && node?.totalEvalsCount ? (
          <Tooltip title={`Evals: ${node?.totalEvalsCount}`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/components/ic_evalsCount.svg`}
                sx={{ width: "12px", height: "15px", color: "text.secondary" }}
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {node?.totalEvalsCount}
              </Typography>
            </Box>
          </Tooltip>
        ) : null}

        {isColumnVisible("annotations") && node?.totalAnnotationsCount > 0 ? (
          <Tooltip title={`Annotations: ${node?.totalAnnotationsCount || 0}`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/components/ic_annotationsCount.svg`}
                sx={{ width: "12px", height: "15px", color: "text.secondary" }}
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {node?.totalAnnotationsCount || 0}
              </Typography>
            </Box>
          </Tooltip>
        ) : null}

        {isColumnVisible("events") && node?.totalEventsCount > 0 ? (
          <Tooltip title={`Events: ${node?.totalEventsCount || 0}`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/components/ic-eventsCount.svg`}
                sx={{ width: "12px", height: "15px", color: "text.secondary" }}
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {node?.totalEventsCount || 0}
              </Typography>
            </Box>
          </Tooltip>
        ) : null}

        {isColumnVisible("cost") && node?.cost > 0 ? (
          <Tooltip title={`Cost: ${node?.cost}`}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                borderRadius: "6px",
                padding: "7px 6px",
              }}
            >
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/components/ic_cost.svg`}
                sx={{ width: "12px", height: "15px", color: "text.secondary" }}
              />
              <Typography
                typography="s3"
                sx={{
                  marginLeft: 0.5,
                }}
              >
                {(() => {
                  const cost = node?.cost;
                  return cost > 0 && cost < 0.0001
                    ? "<0.0001"
                    : `< ${cost.toFixed(4)}`;
                })()}
              </Typography>
            </Box>
          </Tooltip>
        ) : null}
      </Box>
      <Box display={isExpanded ? "block" : "none"}>
        {Array.isArray(children)
          ? children.map((childNode) => (
              <RightSection
                key={childNode.observation_span.id}
                node={childNode.observation_span}
                children={childNode.children}
                columnOptions={columnOptions}
                expanded={expanded}
                setExpanded={setExpanded}
                handleNodeSelect={handleNodeSelect}
                depth={depth + 1}
                selectedNodeId={selectedNodeId}
              />
            ))
          : null}
      </Box>
    </Box>
  );
};

RightSection.propTypes = {
  node: PropTypes.object.isRequired,
  children: PropTypes.array,
  columnOptions: PropTypes.array.isRequired,
  expanded: PropTypes.array.isRequired,
  setExpanded: PropTypes.func.isRequired,
  handleNodeSelect: PropTypes.func.isRequired,
  depth: PropTypes.number,
  selectedNodeId: PropTypes.string,
};
