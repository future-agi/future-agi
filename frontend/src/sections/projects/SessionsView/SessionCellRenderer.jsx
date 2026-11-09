import { Box, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import { parseISO, format, isValid } from "date-fns";
import SvgColor from "src/components/svg-color";
import CustomJsonViewer, {
  RenderJSONString,
} from "src/components/custom-json-viewer/CustomJsonViewer";
import { isJson } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import CustomTooltip from "src/components/tooltip";

const SHOW_TOOLTIPS = ["first_message", "last_message"];

const formatDate = (date) => {
  if (!date) return "-";

  // Truncate to milliseconds if microseconds are present
  let sanitizedDate = date;
  if (typeof date === "string") {
    sanitizedDate = date.replace(/\.(\d{3})\d+/, ".$1");
  }

  try {
    const parsedDate = parseISO(sanitizedDate);
    if (!isValid(parsedDate)) return "-";

    return format(parsedDate, "yyyy/MM/dd, HH:mm:ss");
  } catch (e) {
    return "-";
  }
};

const SessionCellRenderer = (params) => {
  const { column, value, data } = params;
  const colId = column?.colId;

  const renderValue = (fromCell) => {
    // R2D-04: Pass/Fail eval columns (Resolution): 100 → Pass, 0 → Fail.
    if (params.passFail && value != null && value !== "") {
      const n = Number(value);
      const label =
        n >= 100 ? "Pass" : n <= 0 ? "Fail" : `${Math.round(n)}% pass`;
      const pass = n >= 100;
      return (
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            typography: "s2",
            fontWeight: 500,
            color: pass ? "green.600" : n <= 0 ? "red.600" : "text.primary",
          }}
        >
          <Iconify
            icon={
              pass ? "mdi:check-circle-outline" : "mdi:close-circle-outline"
            }
            width={14}
          />
          {label}
        </Box>
      );
    }
    // session_id column — prefer user-defined name (TraceSession.name) over UUID
    if (colId === "session_id") {
      const displayValue = data?.session_name || value;
      return (
        <Box
          sx={{
            color: "text.primary",
            fontWeight: "fontWeightMedium",
            cursor: "pointer",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {displayValue}
        </Box>
      );
    }

    // start_time / end_time columns → formatted date
    if (colId === "start_time" || colId === "end_time") {
      return (
        <Box display={"flex"} justifyContent={"flex-end"}>
          {formatDate(value)}
        </Box>
      );
    }

    // duration column
    if (colId === "duration") {
      return (
        <Box display="flex" alignItems="center" justifyContent={"flex-end"}>
          <Iconify
            icon="radix-icons:clock"
            width={14}
            marginRight="7px"
            sx={{ flexShrink: 0 }}
          />
          {value}s
        </Box>
      );
    }

    if (colId === "user_phone_number") {
      return (
        <Box display="flex" alignItems="center" justifyContent={"flex-start"}>
          {value}
        </Box>
      );
    }

    // total_cost column
    if (colId === "total_cost") {
      return (
        <Box
          display="flex"
          alignItems="center"
          justifyContent={"flex-end"}
          gap={1}
        >
          <Iconify
            icon="mage:dollar"
            color="text.primary"
            height={16}
            width={16}
            sx={{ flexShrink: 0 }}
          />
          {value}
        </Box>
      );
    }
    if (colId === "total_tokens") {
      return (
        <Box
          display="flex"
          alignItems="center"
          justifyContent={"flex-end"}
          gap={1}
        >
          <SvgColor
            src="/assets/icons/ic_tokens.svg"
            color="text.primary"
            sx={{ width: 16, height: 16, flexShrink: 0 }}
          />
          {value}
        </Box>
      );
    }

    if (colId === "total_traces_count") {
      return (
        <Box
          display="flex"
          alignItems="center"
          justifyContent={"flex-end"}
          gap={1}
        >
          {value}
        </Box>
      );
    }

    // fallback for other columns
    return (
      <Box
        sx={{
          height: "100%",
          width: "100%",
        }}
      >
        {value ? (
          typeof value === "object" ? (
            fromCell ? (
              <RenderJSONString val={value} />
            ) : (
              <CustomJsonViewer
                object={isJson(value) ? JSON.parse(value) : value}
              />
            )
          ) : (
            value
          )
        ) : (
          <Typography
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
              width: "100%",
            }}
          >
            {" "}
            -
          </Typography>
        )}
      </Box>
    );
  };

  return (
    <CustomTooltip
      show={SHOW_TOOLTIPS.includes(colId)}
      title={
        <Box sx={{ maxHeight: 200, minWidth: "200px", overflowY: "auto" }}>
          {renderValue()}
        </Box>
      }
    >
      <Box
        sx={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "flex-end",
        }}
        className="session-cell-text"
      >
        {renderValue(true)}
      </Box>
    </CustomTooltip>
  );
};

export default SessionCellRenderer;
