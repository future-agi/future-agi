import React, { useMemo, useState, forwardRef } from "react";
import TraceGrid from "../../LLMTracing/TraceGrid";
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import { getFilterExtraProperties } from "src/utils/prototypeObserveUtils";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { DEFAULT_DATE_FILTER } from "../common";
import { useUserTraceFilter } from "./useUserTraceFilter";
import useTraceSessionStore from "../Store/useTraceSessionStore";
import { useParams } from "react-router";

const UserTraceTab = forwardRef(
  (
    { filters, setFilters, setFilterOpen, traceColumns, setTraceColumns },
    ref,
  ) => {
    const { userId: selectedUserId } = useParams();
    const [_, setLoading] = useState(false);
    const [selectedProjectId] = useUrlState("projectId", null);
    const { cellHeight } = useTraceSessionStore();

    const [dateFilter] = useUrlState("dateFilter", DEFAULT_DATE_FILTER);

    const { validatedFilters } = useUserTraceFilter(
      filters,
      dateFilter, // Pass dateFilter from parent
      traceColumns,
      getFilterExtraProperties,
    );

    const filtersWithUserId = useMemo(() => {
      const hasUserIdFilter = validatedFilters.some(
        (f) => f.column_id === "user_id",
      );

      if (hasUserIdFilter || !selectedUserId) {
        return validatedFilters;
      }

      return [
        ...validatedFilters,
        {
          column_id: "user_id",
          filter_config: {
            filter_op: "equals",
            filter_type: "text",
            filter_value: selectedUserId,
          },
        },
      ];
    }, [validatedFilters, selectedUserId]);

    return (
      <Box sx={{ px: 1.5 }}>
        <TraceGrid
          columns={traceColumns}
          setColumns={setTraceColumns}
          filters={filtersWithUserId}
          ref={ref}
          setFilters={setFilters}
          setFilterOpen={setFilterOpen}
          setSelectedTraces={() => {}}
          setSelectedSpans={() => {}}
          setLoading={setLoading}
          projectId={selectedProjectId}
          cellHeight={cellHeight}
        />
      </Box>
    );
  },
);

UserTraceTab.displayName = "UserTraceTab";

UserTraceTab.propTypes = {
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.func,
  traceColumns: PropTypes.array,
  setTraceColumns: PropTypes.func,
};

export default UserTraceTab;
