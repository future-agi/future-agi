import PropTypes from "prop-types";
import {
  Box,
  Typography,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  LinearProgress,
  Chip,
  Button,
  Alert,
} from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import AttributeValueChart from "./AttributeValueChart";

const AttributeDetail = ({ projectId, attributeKey }) => {
  const queryKey = ["span-attribute-detail", projectId, attributeKey];
  const {
    data: detail,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey,
    queryFn: () =>
      axios.get(endpoints.project.spanAttributeDetail(), {
        params: { project_id: projectId, key: attributeKey },
      }),
    select: (data) => data.data,
    enabled: Boolean(projectId) && Boolean(attributeKey),
    retry: false,
    refetchInterval: (query) => {
      const payload = query.state.data?.data;
      if (payload?.query_refresh_failed) return false;
      return payload?.query_status === "pending" || payload?.query_refreshing
        ? 1000
        : false;
    },
    meta: { errorHandled: true },
  });
  const refreshMutation = useMutation({
    mutationFn: () =>
      axios.get(endpoints.project.spanAttributeDetail(), {
        params: {
          project_id: projectId,
          key: attributeKey,
          refresh: true,
        },
      }),
    onSuccess: () => refetch(),
    meta: { errorHandled: true },
  });

  if (!attributeKey) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          color: "text.secondary",
        }}
      >
        <Typography variant="body2">
          Select an attribute to view details
        </Typography>
      </Box>
    );
  }

  if (detail?.query_status === "pending" && detail?.query_refresh_failed) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={refreshMutation.isPending}
              onClick={() => refreshMutation.mutate()}
            >
              Retry
            </Button>
          }
        >
          Exact attribute details could not be prepared. Retry when you are
          ready.
        </Alert>
      </Box>
    );
  }

  if (isLoading || detail?.query_status === "pending") {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <Box sx={{ textAlign: "center" }}>
          <CircularProgress size={24} />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Loading attribute details…
          </Typography>
        </Box>
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button size="small" onClick={() => refetch()}>
              Retry
            </Button>
          }
        >
          Attribute details could not be loaded.
        </Alert>
      </Box>
    );
  }

  if (!detail) return null;

  return (
    <Box sx={{ flex: 1, p: 2.5, overflow: "auto" }}>
      <Box sx={{ mb: 3 }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", gap: 2 }}>
          <Typography variant="h6" sx={{ mb: 0.5, wordBreak: "break-all" }}>
            {detail.key}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={refreshMutation.isPending || detail.query_refreshing}
            onClick={() => refreshMutation.mutate()}
          >
            {refreshMutation.isPending || detail.query_refreshing
              ? "Refreshing"
              : "Refresh"}
          </Button>
        </Box>
        <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
          {(detail.types?.length
            ? detail.types
            : detail.type
              ? [{ type: detail.type }]
              : []
          ).map(({ type, count }) => (
            <Chip
              key={type}
              label={
                Number.isFinite(count)
                  ? `${type} (${count.toLocaleString()})`
                  : type
              }
              size="small"
              variant="outlined"
              color={
                type === "string"
                  ? "info"
                  : type === "number"
                    ? "warning"
                    : "success"
              }
            />
          ))}
          <Typography variant="body2" color="text.secondary">
            {detail.count?.toLocaleString()} spans
          </Typography>
          {Number.isFinite(detail.unique_values) && (
            <Typography variant="body2" color="text.secondary">
              {detail.unique_values} unique values
            </Typography>
          )}
        </Box>
      </Box>

      {detail.query_refresh_failed && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          The latest refresh could not finish. The last exact result is still
          shown; retry when ready.
        </Alert>
      )}

      {!detail.type && detail.count === 0 && (
        <Typography variant="body2" color="text.secondary">
          No values found for this attribute in the selected data window.
        </Typography>
      )}

      {detail.top_values && detail.top_values.length > 0 && (
        <>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Value Distribution
          </Typography>
          <AttributeValueChart data={detail.top_values} type={detail.type} />

          <TableContainer component={Paper} variant="outlined" sx={{ mt: 2 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Value</TableCell>
                  {detail.types?.length > 1 && <TableCell>Type</TableCell>}
                  <TableCell align="right">Count</TableCell>
                  <TableCell align="right">%</TableCell>
                  <TableCell sx={{ width: 120 }}></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {detail.top_values.map((row) => (
                  <TableRow
                    key={`${row.type || detail.type}:${JSON.stringify(row.value)}`}
                  >
                    <TableCell
                      sx={{
                        maxWidth: 200,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {String(row.value)}
                    </TableCell>
                    {detail.types?.length > 1 && (
                      <TableCell>{row.type}</TableCell>
                    )}
                    <TableCell align="right">
                      {row.count?.toLocaleString()}
                    </TableCell>
                    <TableCell align="right">
                      {row.percentage?.toFixed(1)}%
                    </TableCell>
                    <TableCell>
                      <LinearProgress
                        variant="determinate"
                        value={row.percentage || 0}
                        sx={{ height: 6, borderRadius: 1 }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}

      {detail.stats && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Statistics
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 1.5,
            }}
          >
            {Object.entries(detail.stats).map(([key, value]) => (
              <Paper
                key={key}
                variant="outlined"
                sx={{ p: 1.5, textAlign: "center" }}
              >
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ textTransform: "uppercase" }}
                >
                  {key}
                </Typography>
                <Typography variant="h6">
                  {typeof value === "number"
                    ? value.toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })
                    : value}
                </Typography>
              </Paper>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
};

AttributeDetail.propTypes = {
  projectId: PropTypes.string,
  attributeKey: PropTypes.string,
};

export default AttributeDetail;
