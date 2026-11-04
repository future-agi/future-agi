import ErrorState from "src/components/error-state";
import React, { useEffect, useRef } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { Box, Typography, useTheme, Skeleton } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { format } from "date-fns";

const PAGE_SIZE = 10;

const LogSkeleton = ({ count = 5 }) => {
  const theme = useTheme();

  return (
    <>
      {[...Array(count)].map((_, index) => (
        <Box
          key={index}
          sx={{
            display: "flex",
            alignItems: "flex-start",
            gap: theme.spacing(1.5),
            py: theme.spacing(1),
            borderBottom: "1px solid",
            borderColor: "border.subtle",
          }}
        >
          <Skeleton variant="rounded" width={28} height={28} />
          <Box
            display="flex"
            flexDirection="column"
            gap={theme.spacing(0.5)}
            flex={1}
          >
            <Skeleton variant="rectangular" height={16} width="80%" />
            <Skeleton variant="rectangular" height={14} width="40%" />
          </Box>
        </Box>
      ))}
    </>
  );
};

const ManageLogs = ({ alertId }) => {
  const bottomRef = useRef();
  const theme = useTheme();

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    // @ts-ignore
  } = useInfiniteQuery({
    queryKey: ["alert-logs", alertId],
    enabled: !!alertId,
    queryFn: ({ pageParam = 0 }) =>
      axiosInstance
        .get(endpoints.project.getAlertDetails(alertId), {
          params: {
            page_number: pageParam,
            page_size: PAGE_SIZE,
          },
        })
        .then((res) => {
          return res.data.result.logs;
        }),
    getNextPageParam: (lastPage, pages) => {
      const currentPage = pages.length - 1;
      const totalPages = lastPage.metadata?.total_pages || 0;
      const nextPage = currentPage + 1;
      return nextPage < totalPages ? nextPage : undefined;
    },
  });

  // IntersectionObserver to load more
  useEffect(() => {
    if (!bottomRef.current || !hasNextPage || isFetchingNextPage) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      {
        threshold: 1,
        rootMargin: "20px",
      },
    );

    const currentRef = bottomRef.current;
    observer.observe(currentRef);

    return () => {
      if (currentRef) {
        observer.unobserve(currentRef);
      }
      observer.disconnect();
    };
  }, [hasNextPage, fetchNextPage, isFetchingNextPage]);

  if (isLoading) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        height={"650px"}
        overflow="auto"
        gap={theme.spacing(2)}
      >
        <LogSkeleton count={5} />
      </Box>
    );
  }
  if (isError) {
    return <ErrorState title="Failed to load logs" />;
  }

  return (
    <Box display="flex" flexDirection="column" height={"650px"} overflow="auto">
      {data?.pages?.map((page, pageIndex) =>
        page.results?.map((log, index) => (
          <Box
            key={`${log.timestamp}-${pageIndex}-${index}`}
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: theme.spacing(1.5),
              py: theme.spacing(1),
              borderBottom: "1px solid",
              borderColor: "border.subtle",
            }}
          >
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: "6px",
                backgroundColor:
                  log.type === "ALERT" ? "status.danger.bg" : "status.info.bg",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color:
                  log.type === "ALERT" ? "status.danger.fg" : "status.info.fg",
                flexShrink: 0,
              }}
            >
              {log.type === "ALERT" ? (
                <Iconify
                  icon="solar:danger-circle-linear"
                  width={16}
                  height={16}
                />
              ) : (
                <Iconify
                  icon="solar:info-circle-linear"
                  width={16}
                  height={16}
                />
              )}
            </Box>
            <Box
              display={"flex"}
              flexDirection="column"
              gap={theme.spacing(0.5)}
              flex={1}
            >
              <Typography
                variant="s2_1"
                color="text.primary"
                fontWeight={"fontWeightRegular"}
              >
                {log.message}
              </Typography>
              <Typography
                variant="s3"
                color="text.tertiary"
                fontWeight={"fontWeightRegular"}
              >
                {log.timestamp
                  ? format(new Date(log.timestamp), "dd MMM, yyyy, h:mmaaa")
                  : ""}
              </Typography>
            </Box>
          </Box>
        )),
      )}
      {isFetchingNextPage ? <LogSkeleton count={5} /> : null}
      <div ref={bottomRef} style={{ height: 1 }} />
    </Box>
  );
};

ManageLogs.propTypes = {
  alertId: PropTypes.string,
};

LogSkeleton.propTypes = {
  count: PropTypes.number,
};

export default ManageLogs;
