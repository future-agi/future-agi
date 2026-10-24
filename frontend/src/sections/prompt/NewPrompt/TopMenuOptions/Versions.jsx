import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import { useParams } from "react-router";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import DrawerHeaderbar from "./DrawerHeaderbar";

const Versions = (props) => {
  const {
    onClose,
    versionList,
    versionIndex,
    setVersionIndex,
    setVersionList,
    total,
  } = props;
  const { id } = useParams();
  const [nextPage, setNextPage] = useState(2);

  const versions = useMutation({
    /**
     *
     * @param {Object} variables
     * @param {string} variables.id
     * @param {number} variables.page
     *
     */
    mutationFn: ({ id, page }) =>
      axios.get(endpoints.develop.runPrompt.getPromptVersions(id, page)),
    onSuccess: (data) => {
      const additions = data?.data?.results;

      if (additions) {
        setVersionList([...versionList, ...additions]);
        if (data?.data?.current_page === data?.data?.total_pages) {
          setNextPage(0);
        } else {
          setNextPage(data?.data?.current_page);
        }
      }
    },
  });

  const ref = useScrollEnd(() => {
    if (nextPage > 0 && versionList.length !== total) {
      versions.mutate({
        id: id,
        page: nextPage,
      });
    }
  }, [nextPage]);

  const formatDateString = (/** @type {string} */ timestamp) => {
    // Parse the timestamp into a Date object
    const date = new Date(timestamp);

    // Format the date to "Jan 09, 2025 at 08:00"
    const formattedDate = date
      .toLocaleString("en-US", {
        month: "short",
        day: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
      .replace(",", " at");
    return formattedDate;
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: "16px 8px 0 8px",
        flex: "1",
      }}
    >
      <DrawerHeaderbar title="Versions" onClose={() => onClose()} />

      {/* main wrapper */}
      <Box
        padding="0 16px 16px 16px"
        overflow={"auto"}
        ref={ref}
        // sx={{backgroundColor: "yellow"}}
      >
        {/* single version */}
        {versionList?.map((item, index) => {
          const time = formatDateString(item.created_at);
          const variables = Object.keys(item?.variable_names ?? {});
          return (
            <Box
              key={index}
              padding="16px 24px"
              sx={{
                border: "1px solid",
                borderColor:
                  versionIndex === index ? "primary.main" : "divider",
                borderRadius: "8px",
                marginBottom: "11px",
                "&:hover": {
                  cursor: "pointer",
                  backgroundColor: "background.neutral",
                },
              }}
            >
              <Box
                display="flex"
                flexDirection="column"
                gap={1}
                sx={{}}
                onClick={() => {
                  setVersionIndex(index);
                  onClose();
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <Typography variant="body2" color="text.tertiary">
                    {item.templateVersion}
                  </Typography>
                  {item.isDraft ? (
                    <Chip color="warning" variant="soft" label="Draft" />
                  ) : null}
                </Box>
                <Typography variant="subtitle2" fontWeight="fontWeightRegular">
                  {time}
                </Typography>
                <Typography
                  variant="caption"
                  fontWeight="fontWeightBold"
                  color={
                    variables.length ? "status.success.fg" : "text.secondary"
                  }
                >
                  {variables.length
                    ? variables.reduce((acc, item) => `${acc}{{${item}}} `, "")
                    : "No variables"}
                </Typography>
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

Versions.propTypes = {
  onClose: PropTypes.func.isRequired,
  versionList: PropTypes.array.isRequired,
  total: PropTypes.number,
  versionIndex: PropTypes.number,
  setVersionIndex: PropTypes.func,
  setVersionList: PropTypes.func,
};

export default Versions;
