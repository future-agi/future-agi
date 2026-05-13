/**
 * Workspace breakdown table — per-workspace usage for a dimension.
 * Sortable table showing which workspaces drive the most usage.
 */

import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Paper,
} from "@mui/material";
import { useState } from "react";

import axios, { endpoints } from "src/utils/axios";

function formatUsageCompact(value, _unit) {
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(1);
}

WorkspaceBreakdown.propTypes = {
  dimension: PropTypes.string.isRequired,
  period: PropTypes.string,
  periodEnd: PropTypes.string,
  displayUnit: PropTypes.string,
};

export default function WorkspaceBreakdown({
  dimension,
  period,
  periodEnd,
  displayUnit,
}) {
  const [orderBy, setOrderBy] = useState("usage");
  const [order, setOrder] = useState("desc");

  const { data: workspaces, isLoading } = useQuery({
    queryKey: ["v2-workspace-breakdown", dimension, period, periodEnd],
    queryFn: () =>
      axios.get(endpoints.settings.v2.usageWorkspaceBreakdown, {
        params: { dimension, period, ...(periodEnd ? { period_end: periodEnd } : {}) },
      }),
    select: (res) => res.data?.result?.workspaces || [],
    enabled: !!dimension,
  });

  const sortedWorkspaces = [...(workspaces || [])].sort((a, b) => {
    const multiplier = order === "desc" ? -1 : 1;
    if (orderBy === "usage") return multiplier * (a.usage - b.usage);
    return multiplier * a.workspace_name.localeCompare(b.workspace_name);
  });

  const totalUsage = (workspaces || []).reduce((sum, w) => sum + w.usage, 0);

  const handleSort = (field) => {
    if (orderBy === field) {
      setOrder(order === "asc" ? "desc" : "asc");
    } else {
      setOrderBy(field);
      setOrder("desc");
    }
  };

  if (isLoading) {
    return <Skeleton variant="rounded" height={200} />;
  }

  if (!workspaces || workspaces.length === 0) {
    return (
      <Box
        sx={{
          py: 3,
          textAlign: "center",
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 2,
        }}
      >
        <Typography variant="body2" color="text.disabled">
          No workspace breakdown available
        </Typography>
      </Box>
    );
  }

  return (
    <TableContainer
      component={Paper}
      variant="outlined"
      sx={{ borderRadius: 2 }}
    >
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>
              <TableSortLabel
                active={orderBy === "workspace_name"}
                direction={orderBy === "workspace_name" ? order : "asc"}
                onClick={() => handleSort("workspace_name")}
              >
                Workspace
              </TableSortLabel>
            </TableCell>
            <TableCell align="right">
              <TableSortLabel
                active={orderBy === "usage"}
                direction={orderBy === "usage" ? order : "desc"}
                onClick={() => handleSort("usage")}
              >
                Usage ({displayUnit})
              </TableSortLabel>
            </TableCell>
            <TableCell align="right">% of Total</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {sortedWorkspaces.map((w, i) => (
            <TableRow
              key={w.workspace_id || i}
              hover
              sx={{ "&:last-child td": { borderBottom: 0 } }}
            >
              <TableCell>
                <Typography variant="body2" fontWeight={500}>
                  {w.workspace_name}
                </Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2">
                  {formatUsageCompact(w.usage, displayUnit)}
                </Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2" color="text.secondary">
                  {totalUsage > 0
                    ? ((w.usage / totalUsage) * 100).toFixed(1)
                    : 0}
                  %
                </Typography>
              </TableCell>
            </TableRow>
          ))}
          {/* Total row */}
          <TableRow sx={{ bgcolor: "action.hover" }}>
            <TableCell>
              <Typography variant="subtitle2">Total</Typography>
            </TableCell>
            <TableCell align="right">
              <Typography variant="subtitle2">
                {formatUsageCompact(totalUsage, displayUnit)}
              </Typography>
            </TableCell>
            <TableCell align="right">
              <Typography variant="subtitle2">100%</Typography>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </TableContainer>
  );
}
