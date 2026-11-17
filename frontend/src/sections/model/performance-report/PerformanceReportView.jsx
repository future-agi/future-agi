import {
  Box,
  IconButton,
  InputAdornment,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { format } from "date-fns";
import React, { useState } from "react";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { useScrollEnd } from "src/hooks/use-scroll-end";

const PerformanceReportView = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const { id } = useParams();

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const queryClient = useQueryClient();

  const { data, isLoading, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ["performance-report-list", debouncedSearchQuery],
      queryFn: ({ pageParam }) =>
        axios.get(endpoints.performanceReport.list(id), {
          params: { search_query: debouncedSearchQuery, page: pageParam },
        }),
      initialPageParam: 1,
      getNextPageParam: (o) => {
        return o?.data?.next ? o?.data?.current_page + 1 : null;
      },
    });

  const reports = data?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.results],
    [],
  );

  const { mutate: deleteReport } = useMutation({
    mutationFn: (reportId) =>
      axios.delete(endpoints.performanceReport.delete(id, reportId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["performance-report-list"] });
      enqueueSnackbar("Report deleted successfully", { variant: "success" });
    },
  });

  const navigate = useNavigate();

  const theme = useTheme();

  const tableRef = useScrollEnd(() => {
    if (!isLoading || !isFetchingNextPage) {
      fetchNextPage();
    }
  }, []);

  return (
    <Box sx={{ overflow: "auto" }}>
      <Box sx={{ padding: "20px" }}>
        <TextField
          value={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
          }}
          size="small"
          sx={{ flex: 1, minWidth: "415px" }}
          placeholder="Search"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifier-linear" width={16} />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <Box sx={{ overflow: "auto" }}>
        {isLoading ? <LinearProgress /> : null}
        <TableContainer
          ref={tableRef}
          sx={{ overflow: "auto", height: "calc(100vh - 210px)" }}
        >
          <Table stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Report Name</TableCell>
                <TableCell>Date Created</TableCell>
                <TableCell>Action</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {reports?.map((report) => (
                <TableRow
                  key={report.id}
                  onClick={() => {
                    navigate(`/dashboard/models/${id}/performance`, {
                      state: { report },
                    });
                  }}
                  sx={{
                    "&:hover": {
                      cursor: "pointer",
                      backgroundColor: `${theme.palette.primary.light}11`,
                    },
                  }}
                >
                  <TableCell>{report.name}</TableCell>
                  <TableCell>
                    {format(new Date(report.created_at), "yyyy-MM-dd")}
                  </TableCell>
                  <TableCell sx={{ width: "100px" }}>
                    <IconButton
                      className="row-hover-action"
                      color="error"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteReport(report.id);
                      }}
                      sx={{ display: "flex", flexDirection: "column" }}
                    >
                      <Iconify icon="solar:trash-bin-trash-bold" />
                      <Typography variant="caption" color="text.primary">
                        Delete
                      </Typography>
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
    </Box>
  );
};

export default PerformanceReportView;
