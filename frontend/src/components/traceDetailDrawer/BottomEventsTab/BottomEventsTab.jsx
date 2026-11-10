import PropTypes from "prop-types";
import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Typography,
  Box,
  Grid,
} from "@mui/material";
import Iconify from "src/components/iconify";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";

const BottomEventsTab = ({ spanEvents }) => {
  const [expanded, setExpanded] = useState(null);

  const handleChange = (panel) => (event, isExpanded) => {
    setExpanded(isExpanded ? panel : null);
  };

  // const formatTimestamp = (timestamp) => {
  //   const date = new Date(timestamp / 1000000);
  //   return format(date, "HH:mm:ss.SSS");
  // };

  return (
    <Box sx={{ px: 2 }}>
      {spanEvents?.map((event, index) => (
        <Accordion
          key={index}
          expanded={expanded === `panel${index}`}
          onChange={handleChange(`panel${index}`)}
          sx={{
            mb: 2,
            boxShadow: "none",
            border: "1px solid",
            borderColor: "divider",
            "&:before": {
              display: "none",
            },
          }}
        >
          <AccordionSummary
            expandIcon={
              <Iconify
                icon="solar:alt-arrow-right-linear"
                sx={{ color: "text.secondary" }}
                width={20}
              />
            }
            sx={{
              minHeight: 48,
              px: 1.5,
              flexDirection: "row-reverse",
              "&.Mui-expanded": {
                minHeight: 48,
              },
              "& .MuiAccordionSummary-content": {
                my: 0,
                alignItems: "center",
                width: "100%",
                marginLeft: 0,
                overflow: "hidden",
              },
              "& .MuiAccordionSummary-expandIconWrapper": {
                mr: 1.5,
              },
              "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
                transform: "rotate(90deg)",
              },
            }}
          >
            <Grid
              container
              alignItems="center"
              spacing={2}
              wrap="nowrap"
              sx={{ width: "100%", overflow: "hidden" }}
            >
              <Grid item sx={{ minWidth: 60, flexShrink: 0 }}>
                <Typography
                  variant="s1"
                  sx={{
                    color: "status.info.fg",
                    fontWeight: "fontWeightMedium",
                  }}
                >
                  {event.timestamp}ms
                </Typography>
              </Grid>

              <Grid item sx={{ minWidth: 150, flexShrink: 0, ml: 1 }}>
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "100%",
                  }}
                >
                  {event.name === "exception" ? (
                    <StatusChip label="Exception" status="error" />
                  ) : (
                    <Typography
                      variant="s1"
                      sx={{
                        fontWeight: "fontWeightRegular",
                        textAlign: "center",
                      }}
                    >
                      {event.name}
                    </Typography>
                  )}
                </Box>
              </Grid>

              <Grid item xs sx={{ minWidth: 0, overflow: "hidden", ml: 4 }}>
                <Box
                  sx={{
                    display: "flex",
                    gap: 3,
                    overflow: "hidden",
                    flexWrap: "nowrap",
                  }}
                >
                  {expanded !== `panel${index}` && event.attributes ? (
                    event.name === "exception" ? (
                      <>
                        {event.attributes["exception.type"] && (
                          <Typography
                            variant="s1"
                            sx={{
                              px: 1,
                              py: 0.5,
                              borderRadius: 1,
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              flexShrink: 0,
                            }}
                          >
                            <Box
                              component="span"
                              sx={{ color: "text.primary" }}
                            >
                              exception.type
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "text.secondary", px: 0.5 }}
                            >
                              =
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "status.success.fg" }}
                            >
                              {event.attributes["exception.type"]}
                            </Box>
                          </Typography>
                        )}
                        {event.attributes["exception.message"] && (
                          <Typography
                            variant="s1"
                            sx={{
                              px: 1,
                              py: 0.5,
                              borderRadius: 1,
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              flexShrink: 0,
                            }}
                          >
                            <Box
                              component="span"
                              sx={{ color: "text.primary" }}
                            >
                              exception.message
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "text.secondary", px: 0.5 }}
                            >
                              =
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "status.success.fg" }}
                            >
                              &quot;{event.attributes["exception.message"]}
                              &quot;
                            </Box>
                          </Typography>
                        )}
                      </>
                    ) : (
                      Object.entries(event.attributes)
                        .slice(0, 2)
                        .map(([k, v], attrIndex) => (
                          <Typography
                            key={attrIndex}
                            variant="s1"
                            sx={{
                              px: 1,
                              py: 0.5,
                              fontWeight: "fontWeightRegular",
                              borderRadius: 1,
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              maxWidth: 150,
                              flexShrink: 0,
                            }}
                          >
                            <Box
                              component="span"
                              sx={{ color: "text.primary" }}
                            >
                              {k}
                              <Box
                                component="span"
                                sx={{ color: "text.secondary", px: 0.5 }}
                              >
                                =
                              </Box>
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "status.success.fg" }}
                            >
                              {typeof v === "object"
                                ? JSON.stringify(v)
                                : String(v)}
                            </Box>
                          </Typography>
                        ))
                    )
                  ) : null}
                </Box>
              </Grid>
            </Grid>
          </AccordionSummary>

          <AccordionDetails sx={{ pt: 1, pb: 2, pl: 4 }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "minmax(100px, auto) 1fr",
                gap: 1,
                alignItems: "baseline",
              }}
            >
              {event.attributes &&
                Object.entries(event.attributes).map(([key, value], i) => (
                  <React.Fragment key={i}>
                    <Typography
                      variant="s1"
                      sx={{
                        fontWeight: "fontWeightRegular",
                        mr: 8,
                        color: "text.primary",
                      }}
                    >
                      {key}
                    </Typography>
                    <Typography
                      variant="s1"
                      sx={{
                        wordBreak: "break-word",
                        whiteSpace: "pre-wrap",
                        fontWeight: "fontWeightRegular",
                        color: "status.success.fg",
                      }}
                    >
                      {typeof value === "object"
                        ? JSON.stringify(value, null, 2)
                        : `\u0022${String(value)}\u0022`}
                    </Typography>
                  </React.Fragment>
                ))}
            </Box>
          </AccordionDetails>
        </Accordion>
      ))}
    </Box>
  );
};

BottomEventsTab.propTypes = {
  spanEvents: PropTypes.array,
};

export default BottomEventsTab;
