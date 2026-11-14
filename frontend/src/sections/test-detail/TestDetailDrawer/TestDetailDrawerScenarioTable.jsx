import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import {
  Box,
  Typography,
  IconButton,
  useTheme,
  Collapse,
  Stack,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "../../../components/svg-color/svg-color";
import { ShowComponent } from "src/components/show";
import PersonaComponent from "src/components/persona/personaComponent";

const TestDetailDrawerScenarioTable = ({ data }) => {
  const theme = useTheme();
  const [showFullDetails, setShowFullDetails] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const columnDefs = useMemo(() => {
    const columns = [
      {
        headerName: "Scenario",
        field: "scenario",
      },
    ];

    Object.entries(data?.scenario_columns || {}).forEach(([key, value]) => {
      columns.push({
        headerName: _.startCase(_.toLower(value?.column_name ?? "")),
        field: key,
      });
    });

    return columns;
  }, [data]);

  const rowData = useMemo(() => {
    const row = Object.entries(data?.scenario_columns || {}).reduce(
      (acc, [key, value]) => ({
        ...acc,
        [key]: value?.value,
      }),
      {
        scenario: data?.scenario,
      },
    );
    return [row];
  }, [data]);

  return (
    <Box sx={{ marginX: 2 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px ",
          backgroundColor: "background.default",
          px: 2,
          py: 1.5,
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography typography="title_xs" color="text.primary">
            Scenario Details
          </Typography>
          <IconButton
            onClick={() => setCollapsed(!collapsed)}
            sx={{
              color: "text.primary",
              p: 0,
            }}
            size="small"
          >
            <SvgColor
              sx={{
                height: "24px",
                width: "24px",
                transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
                transition: "transform 0.2s ease",
                transformOrigin: "center",
              }}
              src="/assets/icons/custom/lucide--chevron-down.svg"
            />
          </IconButton>
        </Stack>

        <Collapse in={!collapsed}>
          <Box
            sx={{
              border: "1px solid var(--border-default)",

              backgroundColor: "background.paper",

              padding: "21px",
            }}
          >
            <Box mb={2}>
              <Typography
                color={"text.disabled"}
                typography="s2_1"
                fontWeight="fontWeightRegular"
              >
                SCENARIO
              </Typography>
              <Typography
                fontFamily={"Inter"}
                sx={{ fontSize: 16, fontWeight: 400 }}
              >
                {rowData[0]?.scenario}
              </Typography>
            </Box>

            <Box
              sx={{
                display: "flex",
                gap: 1.5,
                overflowX: "auto",
                flexWrap: "nowrap",
                "&::-webkit-scrollbar": {
                  height: "6px",
                  width: "6px",
                },
                "&::-webkit-scrollbar-track": {
                  backgroundColor: "transparent",
                },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: theme.palette.action.hover,
                  borderRadius: "12px",
                  "&:hover": {
                    backgroundColor: theme.palette.action.selected,
                  },
                },
                scrollbarWidth: "thin",
                scrollbarColor: `${theme.palette.action.hover} transparent`,
              }}
            >
              {/* Render Persona first if it exists */}
              {columnDefs.some((col) => col.headerName === "Persona") && (
                <Box
                  sx={{
                    flex: "0 0 160px",
                    height: showFullDetails ? 300 : 100,
                    overflow: "auto",
                    display: "flex",
                    flexDirection: "column",
                  }}
                >
                  <Typography
                    typography="s2_1"
                    fontWeight={"fontWeightRegular"}
                    sx={{
                      color: "text.disabled",
                      mb: 0.5,
                      textTransform: "none",
                    }}
                  >
                    Persona
                  </Typography>
                  <PersonaComponent
                    formattedValue={
                      rowData[0][
                        columnDefs.find((col) => col.headerName === "Persona")
                          ?.field
                      ]
                    }
                  />
                </Box>
              )}

              {columnDefs
                .filter(
                  (col) =>
                    col.field !== "scenario" && col.headerName !== "Persona",
                )
                .map((col, index) => {
                  const value = rowData[0][col.field];
                  let displayValue;

                  if (typeof value === "object" && value !== null) {
                    displayValue = JSON.stringify(value, null, 2);
                  } else if (value === null || value === undefined) {
                    displayValue = "—";
                  } else {
                    displayValue = value.toString();
                  }

                  const key =
                    typeof col.field === "object" ? `col-${index}` : col.field;

                  return (
                    <Box
                      key={key}
                      sx={{
                        flex: "1 1 0",
                        minWidth: 120,
                        height: showFullDetails ? 300 : 100,
                        overflow: "auto",
                        display: "flex",
                        flexDirection: "column",
                      }}
                    >
                      <Typography
                        typography="s2_1"
                        fontWeight={"fontWeightRegular"}
                        sx={{
                          color: "text.disabled",
                          mb: 0.5,
                          textTransform: "none",
                        }}
                      >
                        {col.headerName}
                      </Typography>
                      <ShowComponent condition={col?.headerName !== "Persona"}>
                        <Typography
                          typography="s1"
                          fontWeight={"fontWeightRegular"}
                          sx={{
                            wordBreak: "break-word",
                            whiteSpace: "pre-wrap",
                            flex: 1,
                          }}
                        >
                          {displayValue}
                        </Typography>
                      </ShowComponent>
                    </Box>
                  );
                })}
            </Box>

            <ButtonBase
              component="div"
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: 0.5,
                height: "18px",
                mt: 1,
                fontSize: "12px",
                color: "primary.main",
              }}
              onClick={() => setShowFullDetails((prev) => !prev)}
            >
              <IconButton size="small" sx={{ padding: 0 }}>
                <Iconify
                  icon={
                    showFullDetails
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={16}
                  height={16}
                  sx={{ color: "primary.main" }}
                />
              </IconButton>
              <Typography fontSize={12} fontWeight={600}>
                {showFullDetails ? "Minimize details" : "View full details"}
              </Typography>
            </ButtonBase>
          </Box>
        </Collapse>
      </Box>
    </Box>
  );
};

TestDetailDrawerScenarioTable.propTypes = {
  data: PropTypes.object,
};

export default TestDetailDrawerScenarioTable;
