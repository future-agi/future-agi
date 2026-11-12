import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import ColumnStatsPopover from "./ColumnStatsPopover";
import {
  ROLE_TAG,
  statsLine,
} from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import {
  addColumnFilter,
  statsActionFilter,
} from "src/sections/develop-detail/DataTab/DataGrid/gridFilterActions";
import { useDataGridViewStore } from "src/sections/develop-detail/DataTab/DataGrid/useDataGridView";

const ROLE_TAG_COLOR = {
  input: "text.secondary",
  expected: "success.dark",
  metadata: "info.dark",
};

/**
 * Line 2 of a versioned Data grid header (area-datasets G3, G5): the role
 * tag of a user column, then the stats line. A part with an action filters
 * the grid; anywhere else opens the stats popover.
 */
const HeaderLine2 = ({ col, api, datasetId }) => {
  const [anchor, setAnchor] = useState(null);
  const isUser = col?.originType === "OTHERS";
  const line = statsLine(col?.stats, { status: col?.status });
  const role = col?.role ?? null;

  const runAction = (action) => {
    if (action === "errors") {
      useDataGridViewStore.getState().patch(datasetId, { cellErrorOnly: true });
      return;
    }
    if (action === "sortWorst" || action === "sortBest") {
      const higher = col?.stats?.higher_is_better !== false;
      const asc = action === "sortWorst" ? higher : !higher;
      api?.applyColumnState({
        state: [{ colId: col.id, sort: asc ? "asc" : "desc" }],
        defaultState: { sort: null },
      });
      return;
    }
    if (action === "hide") {
      api?.setColumnsVisible([col.id], false);
      return;
    }
    const filter = statsActionFilter(action, col);
    if (filter) addColumnFilter(api, filter);
  };

  if (!isUser && !line) return null;
  return (
    <>
      <Box
        role="button"
        tabIndex={-1}
        onClick={(e) => {
          e.stopPropagation();
          if (col?.stats) setAnchor(e.currentTarget);
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          minWidth: 0,
          height: 18,
          cursor: col?.stats ? "pointer" : "default",
          borderRadius: 0.5,
          "&:hover .stats-text": col?.stats ? { color: "text.primary" } : {},
        }}
      >
        {isUser && (
          <Typography
            component="span"
            sx={{
              fontSize: 10,
              lineHeight: "14px",
              fontWeight: 600,
              letterSpacing: "0.04em",
              px: 0.5,
              borderRadius: 0.5,
              flexShrink: 0,
              border: "1px solid",
              borderColor: "divider",
              color: role
                ? ROLE_TAG_COLOR[role] ?? "text.secondary"
                : "text.disabled",
            }}
          >
            {role ? ROLE_TAG[role] ?? role.toUpperCase() : "NO ROLE"}
          </Typography>
        )}
        {line && (
          <Box
            className="stats-text"
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              minWidth: 0,
              overflow: "hidden",
              color: "text.secondary",
              whiteSpace: "nowrap",
            }}
          >
            {line.parts.map((part, i) => (
              <React.Fragment key={part.text}>
                {i > 0 && (
                  <Typography
                    component="span"
                    typography="s3"
                    color="text.disabled"
                  >
                    ·
                  </Typography>
                )}
                <Typography
                  component="span"
                  typography="s3"
                  onClick={
                    part.action
                      ? (e) => {
                          e.stopPropagation();
                          runAction(part.action);
                        }
                      : undefined
                  }
                  sx={{
                    color: part.tone
                      ? part.tone === "warning"
                        ? "warning.dark"
                        : `${part.tone}.main`
                      : "inherit",
                    fontWeight: part.action ? 500 : 400,
                    fontVariantNumeric: "tabular-nums",
                    ...(part.action && {
                      cursor: "pointer",
                      "&:hover": { textDecoration: "underline" },
                    }),
                  }}
                >
                  {part.text}
                </Typography>
              </React.Fragment>
            ))}
            {line.progress != null && (
              <Box
                sx={{
                  width: 40,
                  height: 2,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  flexShrink: 0,
                }}
              >
                <Box
                  sx={{
                    width: `${Math.round(line.progress * 100)}%`,
                    height: "100%",
                    bgcolor: "primary.main",
                    borderRadius: 1,
                  }}
                />
              </Box>
            )}
          </Box>
        )}
      </Box>
      <ColumnStatsPopover
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        column={col}
        onAction={runAction}
      />
    </>
  );
};

HeaderLine2.propTypes = {
  col: PropTypes.object,
  api: PropTypes.any,
  datasetId: PropTypes.string,
};

export const CustomDevelopDetailColumn = (props) => {
  const {
    displayName,
    showColumnMenu,
    col,
    hideMenu,
    eGridHeader,
    api,
    versioned,
    datasetId,
  } = props;
  const refButton = useRef(null);

  useEffect(() => {
    eGridHeader.style.padding = "0px";
  }, [eGridHeader.style]);

  // The versioned grid sizes columns by type (G4); only the legacy grid
  // widens every column to fit its name.
  useEffect(() => {
    if (!versioned && api && col) {
      const minWidth = Math.max(displayName.length * 8 + 50, 250);
      props.api.setColumnWidths([{ key: col.id, newWidth: minWidth }]);
    }
  }, [api, col, displayName, props.api, versioned]);

  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };

  const iconStyle = {
    color: "text.secondary",
  };
  const emptyCount = Number(col?.stats?.empty_count);
  const totalCount = Number(col?.stats?.total);
  const headerTitle =
    totalCount > 0 && emptyCount / totalCount > 0.9
      ? `Empty in ${emptyCount} of ${totalCount} rows`
      : displayName;

  // Dataset column-type icons keep their original set (design-system exception,
  // agreed 2026-10-30); every other icon in this header is Solar.
  const renderIcon = () => {
    if (col.originType === "run_prompt") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: 20, height: 20, color: "info.main" }}
        />
      );
    } else if (col.originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "info.success" }}
        />
      );
    } else if (
      col.originType === "optimisation" ||
      col.originType === "optimisation_evaluation"
    ) {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_optimize.svg`}
          sx={{ width: 20, height: 20, color: "primary.main" }}
        />
      );
    } else if (col.originType === "annotation_label") {
      return <Iconify icon="jam:write" sx={iconStyle} />;
    } else if (col.originType === "vector_db") {
      return <Iconify icon="solar:widget-broken" sx={iconStyle} />;
    } else if (col.originType === "extracted_entities") {
      return <Iconify icon="material-symbols:chip-extraction" sx={iconStyle} />;
    } else if (col.originType === "extracted_json") {
      return <Iconify icon="mdi:code-json" sx={iconStyle} />;
    } else if (col.originType === "python_code") {
      return <Iconify icon="mdi:code" sx={iconStyle} />;
    } else if (col.originType === "classification") {
      return <Iconify icon="mingcute:classify-2-line" sx={iconStyle} />;
    } else if (col.originType === "api_call") {
      return <Iconify icon="hugeicons:api" sx={iconStyle} />;
    } else if (col.originType === "conditional") {
      return <Iconify icon="hugeicons:node-add" sx={iconStyle} />;
    } else if (col.dataType === "text") {
      return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
    } else if (col.dataType === "array") {
      return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (col.dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (col.dataType === "datetime") {
      return <Iconify icon="tabler:calendar" sx={iconStyle} />;
    } else if (col.dataType === "json") {
      return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
    } else if (col.dataType === "image") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_image.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "audio") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_audio.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "document") {
      return (
        <SvgColor
          src={`/assets/icons/files/ic_file_head.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "images") {
      return (
        <Iconify
          icon="material-symbols:art-track-outline"
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  const getBackgroundColor = (originType) => {
    if (
      originType === "evaluation" ||
      originType === "optimisation_evaluation"
    ) {
      return "var(--surface-highlight)";
    } else if (originType === "run_prompt") {
      return "status.info.bg";
    } else if (originType === "optimisation") {
      return "status.discovery.bg";
    } else if (originType === "annotation_label") {
      return "status.discovery.bg";
    }
    return "background.neutral";
  };

  if (versioned) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 0.25,
          width: "100%",
          minWidth: 0,
          backgroundColor: getBackgroundColor(),
          pl: 1,
          pr: 0.25,
          height: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            minWidth: 0,
            height: 22,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              minWidth: 0,
            }}
          >
            <Box
              sx={{
                display: "flex",
                flexShrink: 0,
                "& svg, & span": { width: 16, height: 16 },
              }}
            >
              {renderIcon()}
            </Box>
            <CustomTooltip
              show
              title={headerTitle}
              placement="top"
              size="small"
              arrow
              enterDelay={600}
            >
              <Typography
                noWrap
                typography="s2"
                fontWeight="fontWeightMedium"
                color="text.primary"
                sx={{ minWidth: 0 }}
              >
                {displayName}
              </Typography>
            </CustomTooltip>
          </Box>
          {!hideMenu && (
            <IconButton
              size="small"
              ref={refButton}
              onClick={onMenuClicked}
              sx={{ p: 0.25, flexShrink: 0 }}
              aria-label={`${displayName} column menu`}
            >
              <Iconify icon="mdi:dots-vertical" width={16} />
            </IconButton>
          )}
        </Box>
        <HeaderLine2 col={col} api={api} datasetId={datasetId} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        backgroundColor: getBackgroundColor(),
        paddingX: 1,
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {renderIcon()}
        <Typography fontWeight={500} fontSize="13px" color={"text.secondary"}>
          {displayName}
        </Typography>
      </Box>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="solar:menu-dots-vertical-linear" />
        </IconButton>
      )}
    </Box>
  );
};

CustomDevelopDetailColumn.propTypes = {
  displayName: PropTypes.string.isRequired,
  eSort: PropTypes.object,
  eMenu: PropTypes.object,
  eFilterButton: PropTypes.object,
  eFilter: PropTypes.object,
  eSortOrder: PropTypes.object,
  eSortAsc: PropTypes.object,
  eSortDesc: PropTypes.object,
  eSortNone: PropTypes.object,
  eText: PropTypes.object,
  menuButtonRef: PropTypes.object,
  filterButtonRef: PropTypes.object,
  sortOrderRef: PropTypes.object,
  sortAscRef: PropTypes.object,
  sortDescRef: PropTypes.object,
  sortNoneRef: PropTypes.object,
  filterRef: PropTypes.object,
  showColumnMenu: PropTypes.func,
  col: PropTypes.object,
  hideMenu: PropTypes.bool,
  eGridHeader: PropTypes.any,
  api: PropTypes.any,
  versioned: PropTypes.bool,
  datasetId: PropTypes.string,
};

export default CustomDevelopDetailColumn;
