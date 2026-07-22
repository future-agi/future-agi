import React from "react";
import { Box, Typography, Button } from "@mui/material";
import PropTypes from "prop-types";
import UsersDetailDateTimePicker from "./usersDetailDateTimePicker";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useUrlState } from "src/routes/hooks/use-url-state";
import {
  DEFAULT_DATE_FILTER,
  DEFAULT_ZOOM_RANGE,
  LAST_ACTIVE_STYLES,
} from "../common";
import { useNavigate, useParams } from "react-router";
import { format, formatDistanceToNow } from "date-fns";

const buildUserFilterAndNavigate = ({
  filters,
  paramKey,
  selectedUserId,
  projectId,
  path,
  navigate,
}) => {
  const userIdFilter = {
    column_id: "user_id",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: selectedUserId,
    },
    _meta: {
      parentProperty: "user_id",
    },
    id: `user-${selectedUserId}`,
  };

  // remove invalid filters
  const mergedFilters = (filters || []).filter(
    (f) =>
      f?.column_id &&
      f?.filter_config?.filter_type &&
      f?.filter_config?.filter_op,
  );

  // ensure userId filter is present
  const alreadyHasUserId = mergedFilters.some((f) => f.column_id === "user_id");
  if (!alreadyHasUserId) {
    mergedFilters.push(userIdFilter);
  }

  // build query params
  const searchParams = new URLSearchParams();
  if (mergedFilters.length > 0) {
    searchParams.set(paramKey, JSON.stringify(mergedFilters));
  }

  const queryString = searchParams.toString();
  navigate(
    `/dashboard/observe/${projectId}/${path}${queryString ? `?${queryString}` : ""}`,
  );
};

const UserHeaderSection = ({
  dateFilter,
  setDateFilter,
  isEdit,
  lastActiveDate,
}) => {
  const { userId: selectedUserId } = useParams();
  const [zoomRange, setZoomRange] = useUrlState(
    "zoomRange",
    DEFAULT_ZOOM_RANGE,
  );
  const [_, setDateInterval] = useUrlState("dateInterval", "day");
  const [sessionFilter] = useUrlState("sessionFilter", []);
  const [traceFilter] = useUrlState("traceFilter", []);

  const [selectedProjectId] = useUrlState("projectId", null);
  const navigate = useNavigate();

  return (
    <Box px={2} py={1} display="flex" flexDirection="column" gap={1}>
      {/* Header title */}
      <Box display="flex" alignItems="center" gap={2}>
        <Typography variant="h6">User ID: {selectedUserId}</Typography>
        <Typography sx={LAST_ACTIVE_STYLES}>
          Last Active{" "}
          {lastActiveDate
            ? formatDistanceToNow(lastActiveDate, { addSuffix: true })
            : 0}
          , {lastActiveDate ? format(lastActiveDate, "dd-MM-yyyy, HH:mm") : ""}
        </Typography>
      </Box>

      {/* Row: Date Picker + Refresh | Configure + Share */}
      <Box
        display="flex"
        alignItems="center"
        justifyContent="space-between"
        gap={2}
      >
        {/* Left side: Date picker and refresh */}
        <Box display="flex" alignItems="center" gap={1} flexGrow={1}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <UsersDetailDateTimePicker
              dateFilter={dateFilter}
              setDateFilter={setDateFilter}
              setDateInterval={setDateInterval}
              zoomRange={zoomRange}
              setZoomRange={setZoomRange}
              isEdit={isEdit}
            />
          </Box>
          <Button
            startIcon={<Iconify icon="clarity:refresh-line" />}
            onClick={() => {
              setZoomRange(DEFAULT_ZOOM_RANGE);
              setDateFilter(DEFAULT_DATE_FILTER);
            }}
          >
            <Typography variant="s1" fontWeight={"fontWeightRegular"}>
              Refresh
            </Typography>
          </Button>
        </Box>

        {/* Right side: Configure and Share */}
        <Box display="flex" alignItems="center" gap={1}>
          <Button
            variant="outlined"
            size="small"
            onClick={() =>
              buildUserFilterAndNavigate({
                filters: traceFilter,
                paramKey: "primaryTraceFilter",
                selectedUserId,
                projectId: selectedProjectId,
                path: "llm-tracing",
                navigate,
              })
            }
            sx={{ paddingX: "16px" }}
            startIcon={<SvgColor src="/assets/icons/navbar/ic_llm.svg" />}
          >
            View Traces
          </Button>

          <Button
            variant="outlined"
            size="small"
            onClick={() =>
              buildUserFilterAndNavigate({
                filters: sessionFilter,
                paramKey: "sessionFilter",
                selectedUserId,
                projectId: selectedProjectId,
                path: "sessions",
                navigate,
              })
            }
            sx={{ paddingX: "16px" }}
            startIcon={<SvgColor src="/assets/icons/navbar/ic_sessions.svg" />}
          >
            View Sessions
          </Button>
        </Box>
      </Box>
    </Box>
  );
};

UserHeaderSection.propTypes = {
  dateFilter: PropTypes.array,
  setDateFilter: PropTypes.func,
  isEdit: PropTypes.bool,
  lastActiveDate: PropTypes.string,
};

export default UserHeaderSection;
