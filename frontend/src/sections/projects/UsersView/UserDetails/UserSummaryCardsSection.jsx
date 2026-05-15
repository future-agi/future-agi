import React, { useEffect, useRef, useState } from "react";
import { Box, Grid } from "@mui/material";
import UserHeaderCard from "./UserHeaderCard";
import { useQuery } from "@tanstack/react-query";
import {
  DEFAULT_DATE_FILTER,
  getSummaryCards,
  transformDateFilterToBackendFilters,
} from "../common";
import { useUrlState } from "src/routes/hooks/use-url-state";
import axios, { endpoints } from "src/utils/axios";
import { objectCamelToSnake } from "src/utils/utils";
import { canonicalizeApiFilterColumnIds } from "src/utils/filter-column-ids";
import { parseISO } from "date-fns";
import PropTypes from "prop-types";

const UserSummaryCardsSection = ({ setLastActiveDate }) => {
  const [selectedProjectId] = useUrlState("projectId", null);
  const [selectedEndUserId] = useUrlState("endUserId", null);
  const [dateFilter] = useUrlState("dateFilter", DEFAULT_DATE_FILTER);
  const [dateInterval] = useUrlState("dateInterval", "day");
  const [maxHeight, setMaxHeight] = useState(0);
  const cardRefs = useRef([]);

  const { data: metricsData, isLoading } = useQuery({
    queryKey: [
      "get-graph-data",
      selectedProjectId,
      selectedEndUserId,
      dateFilter,
    ],
    queryFn: async () => {
      const filters = transformDateFilterToBackendFilters(dateFilter);
      const response = await axios.post(endpoints.project.getUserMetrics(), {
        project_id: selectedProjectId,
        end_user_id: selectedEndUserId,
        interval: dateInterval,
        filters: canonicalizeApiFilterColumnIds(objectCamelToSnake(filters)),
      });
      return response.data;
    },
    enabled: Boolean(selectedProjectId && selectedEndUserId),
  });

  const data = metricsData?.result?.[0];
  const summaryCards = getSummaryCards(data);
  useEffect(() => {
    const parsedDate = data?.lastActive ? parseISO(data.lastActive) : null;
    setLastActiveDate(parsedDate);
  }, [data?.lastActive, setLastActiveDate]);

  useEffect(() => {
    if (!cardRefs.current.length) return;

    const observers = cardRefs.current.map((ref) => {
      if (!ref) return null;

      const observer = new ResizeObserver(() => {
        const heights = cardRefs.current.map((r) => r?.offsetHeight || 0);
        setMaxHeight(Math.max(...heights));
      });

      observer.observe(ref);
      return observer;
    });

    return () => {
      observers.forEach((o) => o?.disconnect());
    };
  }, [cardRefs, setMaxHeight]);

  return (
    <Grid container spacing={2}>
      {summaryCards.map((card, index) => (
        <Grid item xs={12} sm={6} md={3} key={index} sx={{ display: "flex" }}>
          <Box
            ref={(el) => (cardRefs.current[index] = el)}
            sx={{ minHeight: maxHeight, flex: 1 }}
          >
            <UserHeaderCard
              title={card.title}
              value={isLoading ? "0" : card.value}
              icon={card.icon}
              color={card.color}
              bgColor={card.bgColor}
              additional_data={card.additional_data}
            />
          </Box>
        </Grid>
      ))}
    </Grid>
  );
};

UserSummaryCardsSection.propTypes = {
  setLastActiveDate: PropTypes.func,
};

export default UserSummaryCardsSection;
