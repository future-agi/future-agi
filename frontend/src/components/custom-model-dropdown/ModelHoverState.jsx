import React from "react";
import { Typography, Divider, Box, Slider, useTheme } from "@mui/material";
import Image from "../image";
import { ShowComponent } from "../show";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import LoadingModelHover from "./LoadingModelHover";
import { LOGO_WITH_BLACK_BACKGROUND } from "./common";

const CellStyle = ({ type = "td", title, description }) => {
  return (
    <Box sx={{ padding: "0 8px" }}>
      <ShowComponent condition={type === "th"}>
        <Typography
          variant="s3"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          {title}
        </Typography>
      </ShowComponent>
      <ShowComponent condition={type === "td"}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <Typography
            variant="s3"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            {title ? (typeof title === "number" ? `$${title}` : title) : "-"}
          </Typography>
          {description && (
            <Typography
              variant="s3"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              {description}
            </Typography>
          )}
        </Box>
      </ShowComponent>
    </Box>
  );
};

CellStyle.propTypes = {
  type: PropTypes.string,
  title: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  description: PropTypes.string,
};

export default function ModelHoverState({ modelName }) {
  const theme = useTheme();

  const { data, isLoading } = useQuery({
    queryKey: ["model-hover-state", modelName],
    queryFn: () =>
      axios.get(endpoints.develop.modelList, {
        params: { name: modelName },
      }),
    select: (d) => d.data?.results?.[0],
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });

  if (isLoading) {
    return <LoadingModelHover />;
  }
  return (
    <Box
      onClick={(e) => e.stopPropagation()}
      sx={{
        marginTop: "-10px",
        padding: "16px",
        backgroundColor: "background.paper",
        borderRadius: "8px",
      }}
    >
      <Box sx={{ display: "flex", gap: 1 }}>
        {data?.logoUrl && (
          <Image
            src={data?.logoUrl}
            alt=""
            width="24px"
            sx={{ verticalAlign: "middle" }}
            flexShrink={0}
            disableThemeFilter={
              !LOGO_WITH_BLACK_BACKGROUND.includes(
                data?.providers?.toLowerCase(),
              )
            }
          />
        )}
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
          sx={{ textTransform: "Uppercase" }}
        >
          {data?.model_name}
        </Typography>
      </Box>
      <Box
        sx={{
          width: "100%",
          overflow: "hidden",
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          marginY: "8px",
        }}
      >
        <table
          border={1}
          width="100%"
          style={{
            borderCollapse: "collapse",
            borderColor: theme.palette.divider,
          }}
        >
          <tr>
            <th style={{ textAlign: "left" }}>
              <CellStyle type="th" title="Input/Output API pricing" />
            </th>
            <th style={{ textAlign: "left" }}>
              <CellStyle type="th" title="Input" />
            </th>
            <th style={{ textAlign: "left" }}>
              <CellStyle type="th" title="Output" />
            </th>
          </tr>
          <tr>
            <td>
              <CellStyle title="<= 200k tokens" />
            </td>
            <td>
              <CellStyle title={data?.pricing?.inputPer1MTokens} />
            </td>
            <td>
              <CellStyle title={data?.pricing?.outputPer1MTokens} />
            </td>
          </tr>
          <tr>
            <td>
              <CellStyle
                title="200k tokens"
                description="(API pricing per 1M tokens , UI remains free of charge)"
              />
            </td>
            <td>
              <CellStyle title="" />
            </td>
            <td>
              <CellStyle title="" />
            </td>
          </tr>
        </table>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: "12px",
          marginY: "8px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Best for
          </Typography>
          <ul style={{ paddingLeft: "16px", margin: "0" }}>
            {data?.bestFor?.map((item, index) => (
              <li key={index} style={{ margin: 0, padding: 0, lineHeight: 0 }}>
                <Typography
                  variant="s3"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  {item}
                </Typography>
              </li>
            ))}
          </ul>
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Use case
          </Typography>
          <ul style={{ paddingLeft: "16px", margin: "0" }}>
            {data?.useCase?.map((item, index) => (
              <li key={index} style={{ margin: 0, padding: 0, lineHeight: 0 }}>
                <Typography
                  variant="s3"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  {item}
                </Typography>
              </li>
            ))}
          </ul>
        </Box>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: "12px",
          marginTop: "8px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              color: "transparent",
            },
            width: "100%",
          }}
        >
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Knowledge cutoff
          </Typography>
          <ul style={{ paddingLeft: "0", margin: "0" }}>
            {data?.cutoff
              ? [data?.cutoff]?.map((item, index) => (
                  <li
                    key={index}
                    style={{ margin: 0, padding: 0, lineHeight: 0 }}
                  >
                    <Typography
                      variant="s3"
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      {item}
                    </Typography>
                  </li>
                ))
              : "-"}
          </ul>
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Rate limits
          </Typography>
          <ul style={{ paddingLeft: "16px", margin: "0" }}>
            {data?.rateLimits?.requestsPerMinute
              ? [data?.rateLimits?.requestsPerMinute]?.map((item, index) => (
                  <li
                    key={index}
                    style={{ margin: 0, padding: 0, lineHeight: 0 }}
                  >
                    <Typography
                      variant="s3"
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      {item} RPM
                    </Typography>
                  </li>
                ))
              : "-"}
          </ul>
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Latency
          </Typography>
          {data?.latency ? (
            <Slider
              size="small"
              // disabled
              valueLabelDisplay="auto"
              max={data?.latency * 1.5}
              defaultValue={data?.latency}
              value={data?.latency}
              aria-label="Small"
              onChange={(e) => e.preventDefault()}
              sx={{ cursor: "default", width: "100%", color: "text.primary" }}
            />
          ) : (
            "-"
          )}
        </Box>
      </Box>
    </Box>
  );
}

ModelHoverState.propTypes = {
  modelName: PropTypes.string,
};
