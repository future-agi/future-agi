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
  Alert,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import AttributeValueChart from "./AttributeValueChart";

const AttributeDetail = ({ projectId, attributeKey, attributeType }) => {
  const { data: detail, isLoading } = useQuery({
    queryKey: ["span-attribute-detail", projectId, attributeKey, attributeType],
    queryFn: () =>
      axios.get(endpoints.project.spanAttributeDetail(), {
        params: {
          project_id: projectId,
          key: attributeKey,
          ...(attributeType ? { type: attributeType } : {}),
        },
      }),
    select: (data) => data.data,
    enabled: Boolean(projectId) && Boolean(attributeKey),
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

  if (isLoading) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (!detail) return null;

  return (
    <Box sx={{ flex: 1, p: 2.5, overflow: "auto" }}>
      {detail.query_complete === false && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Attribute statistics are temporarily incomplete. No zero counts were
          inferred; try again shortly.
        </Alert>
      )}

      <Box sx={{ mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 0.5, wordBreak: "break-all" }}>
          {detail.key}
        </Typography>
        <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
          {detail.type && (
            <Chip
              label={detail.type}
              size="small"
              variant="outlined"
              color={
                detail.type === "string"
                  ? "info"
                  : detail.type === "number"
                    ? "warning"
                    : "success"
              }
            />
          )}
          {typeof detail.count === "number" && (
            <Typography variant="body2" color="text.secondary">
              {detail.count.toLocaleString()} spans
            </Typography>
          )}
          {typeof detail.unique_values === "number" && (
            <Typography variant="body2" color="text.secondary">
              {detail.unique_values} unique values
            </Typography>
          )}
        </Box>
      </Box>

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
                  <TableCell align="right">Count</TableCell>
                  <TableCell align="right">%</TableCell>
                  <TableCell sx={{ width: 120 }}></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {detail.top_values.map((row) => (
                  <TableRow key={row.value}>
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
  attributeType: PropTypes.oneOf(["string", "number", "boolean"]),
};

export default AttributeDetail;
