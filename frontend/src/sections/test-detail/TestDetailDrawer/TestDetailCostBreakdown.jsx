import { Box, Typography, useTheme, Checkbox } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import ApexCharts from "react-apexcharts";
import { ShowComponent } from "src/components/show";
import CostCard from "./CostCard";

const costBreakdownOrder = [
  {
    label: "LLM",
    value: "llm",
  },
  {
    label: "TTS",
    value: "tts",
  },
  {
    label: "STT",
    value: "stt",
  },
];

const TestDetailCostBreakdown = ({ costBreakdown }) => {
  const theme = useTheme();

  // All available data with metadata
  const allData = useMemo(() => {
    const colorMap = {
      llm: theme.palette.blue[700], // blue[700]
      tts: theme.palette.blue[600], // blue[600]
      stt: theme.palette.blue[500], // blue[500]
    };

    return costBreakdownOrder
      .map((curr) => ({
        label: curr.label,
        value: curr.value,
        cost: costBreakdown?.[curr.value]?.cost ?? 0,
        color: colorMap[curr.value],
      }))
      .filter((item) => item.cost > 0);
  }, [costBreakdown, theme.palette.blue]);

  // State to track which items are visible
  const [visibleItems, setVisibleItems] = useState(() =>
    allData.reduce((acc, item) => ({ ...acc, [item.value]: true }), {}),
  );

  // Filtered series based on visible items
  const { series, labels, colors } = useMemo(() => {
    const seriesData = [];
    const labelsData = [];
    const colorsData = [];

    allData.forEach((item) => {
      if (visibleItems[item.value]) {
        seriesData.push(item.cost);
        labelsData.push(item.label);
        colorsData.push(item.color);
      }
    });

    return { series: seriesData, labels: labelsData, colors: colorsData };
  }, [allData, visibleItems]);

  const toggleItem = (value) => {
    const visibleCount = Object.values(visibleItems).filter((v) => v).length; //if one item is visible and user clicks on it, show all items
    if (visibleCount === 1 && visibleItems[value]) {
      setVisibleItems(
        allData.reduce((acc, item) => ({ ...acc, [item.value]: true }), {}),
      );
    } else {
      setVisibleItems((prev) => ({
        ...prev,
        [value]: !prev[value],
      }));
    }
  };

  const pieOptions = {
    chart: {
      type: "donut",
      events: {
        dataPointMouseEnter: ({ target }, ctx) => {
          if (target.getAttribute("data:pieClicked") !== "true") {
            const sliceIndex = parseInt(target.attributes.j.value);
            ctx.pie.pieClicked(sliceIndex);
          }
        },
        dataPointMouseLeave: ({ target }, ctx) => {
          if (target.getAttribute("data:pieClicked") === "true") {
            const sliceIndex = parseInt(target.attributes.j.value);
            ctx.pie.pieClicked(sliceIndex);
          }
        },
      },
    },
    states: {
      hover: {
        filter: {
          type: "none",
        },
      },
    },
    labels: labels,
    colors: colors,
    legend: {
      show: false,
    },
    plotOptions: {
      pie: {
        donut: {
          size: "40%",
          labels: {
            show: true,
            value: {
              fontWeight: 600,
              fontSize: "18px",
              fontFamily: "IBM Plex Sans",
              formatter: (val) => {
                return `$${Number(val).toFixed(2)}`;
              },
            },
            total: {
              show: true,
              showAlways: true,
              label: "Total Cost",
              fontWeight: 600,
              fontSize: "14px",
              fontFamily: "IBM Plex Sans",
              color: "var(--text-secondary)",
              formatter: (w) => {
                const total = w.globals.seriesTotals.reduce((a, b) => a + b, 0);
                return `$${total.toFixed(2)}`;
              },
            },
          },
        },
      },
    },
    dataLabels: {
      enabled: true,
      formatter: (val, opts) => {
        const actualValue = opts.w.config.series[opts.seriesIndex];
        return [`$${Number(actualValue).toFixed(2)}`, `${val.toFixed(1)}%`];
      },
      style: {
        fontSize: "14px",
        fontFamily: "IBM Plex Sans",
        fontWeight: 600,
        colors: ["#fff"],
      },
      dropShadow: {
        enabled: false,
      },
    },
    tooltip: {
      enabled: true,
      custom: ({ series, seriesIndex, _, w }) => {
        const label = w.globals.labels[seriesIndex];
        const value = series[seriesIndex];
        const color = w.config.colors[seriesIndex];

        return `
          <div style="
            background: var(--bg-paper);
            padding: 12px 16px;
            border-radius: 4px;
            border: 1px solid var(--border-default);
            font-family: 'IBM Plex Sans', sans-serif;
            min-width: 150px;
          ">
            <div style="
              font-weight: 600;
              font-size: 12px;
              color: var(--text-secondary);
              margin-bottom: 4px;
              text-transform: uppercase;
              letter-spacing: 0.5px;
            ">
              ${label}
            </div>
            <div style="
              display: flex;
              align-items: center;
              gap: 8px;
              margin-top: 8px;
            ">
              <div style="
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: ${color};
              "></div>
              <div style="
                font-size: 13px;
                color: var(--text-muted);
              ">
                ${label}
              </div>
            </div>
            <div style="
              font-weight: 600;
              font-size: 16px;
              color: var(--text-primary);
              margin-top: 4px;
            ">
              $${Number(value).toFixed(2)}
            </div>
          </div>
        `;
      },
    },
  };

  const isCostPreset = series.some((item) => item > 0);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography typography="s1_2" fontWeight="fontWeightMedium">
        Cost Breakdown (Call cost distribution)
      </Typography>
      <ShowComponent condition={isCostPreset}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Box sx={{ flex: "1 1 0", minWidth: 0 }}>
            <ApexCharts
              options={pieOptions}
              series={series}
              type="donut"
              height="280px"
            />
          </Box>
          {/* Custom Legend with Checkboxes */}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              flexShrink: 0,
            }}
          >
            {allData.map((item) => {
              const percentage = (
                (item.cost /
                  allData.reduce(
                    (sum, i) => sum + (visibleItems[i.value] ? i.cost : 0),
                    0,
                  )) *
                100
              ).toFixed(1);

              return (
                <Box
                  key={item.value}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    cursor: "pointer",
                    padding: "4px 8px",
                    borderRadius: "4px",
                    "&:hover": {
                      backgroundColor: "action.hover",
                    },
                  }}
                  onClick={() => toggleItem(item.value)}
                >
                  <Checkbox
                    checked={visibleItems[item.value]}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleItem(item.value);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    size="small"
                    sx={{
                      padding: 0,
                      color: "text.disabled",
                      "&.Mui-checked": {
                        color: "primary.light",
                        borderColor: "primary.light",
                      },
                    }}
                  />
                  <Box
                    sx={{
                      width: 9,
                      height: 9,
                      borderRadius: "2px",
                      backgroundColor: item.color,
                      flexShrink: 0,
                    }}
                  />
                  <Box>
                    <Typography
                      typography="s3"
                      component="span"
                      sx={{
                        flex: 1,
                        color: visibleItems[item.value]
                          ? "text.primary"
                          : "text.disabled",
                      }}
                    >
                      {item.label}:
                    </Typography>
                    <Typography
                      typography="s2"
                      fontWeight="fontWeightMedium"
                      component="span"
                    >
                      {" "}
                      ${item.cost.toFixed(2)} ({" "}
                      {visibleItems[item.value] ? percentage : "0.0"}%)
                    </Typography>
                  </Box>
                </Box>
              );
            })}
          </Box>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!isCostPreset}>
        <Box
          sx={{
            height: "300px",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Typography typography="s1_2" color="text.disabled">
            All costs are $0 for this call
          </Typography>
        </Box>
      </ShowComponent>
      <Box
        sx={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 1 }}
      >
        <CostCard
          title="Speech-to-text cost"
          icon="/assets/icons/simulate/ic_speach_to_text.svg"
          iconColor="orange.500"
          iconBgColor="orange.o5"
          value={costBreakdown?.stt?.cost ?? 0}
        />
        <CostCard
          title="LLM cost"
          icon="/assets/icons/simulate/ic_llm.svg"
          iconColor="primary.main"
          iconBgColor="action.hover"
          value={costBreakdown?.llm?.cost ?? 0}
          additionalInfo={[
            {
              label: "Prompt tokens",
              value: costBreakdown?.llm?.promptTokens ?? 0,
            },
            {
              label: "Completion tokens",
              value: costBreakdown?.llm?.completionTokens ?? 0,
            },
          ]}
        />
        <CostCard
          title="Text-to-speech cost"
          icon="/assets/icons/simulate/ic_text_to_speach.svg"
          iconColor="blue.500"
          iconBgColor="blue.o5"
          value={costBreakdown?.tts?.cost ?? 0}
        />
      </Box>
    </Box>
  );
};

TestDetailCostBreakdown.propTypes = {
  costBreakdown: PropTypes.shape({
    llm: PropTypes.shape({
      cost: PropTypes.number,
      promptTokens: PropTypes.number,
      completionTokens: PropTypes.number,
    }),
    stt: PropTypes.shape({
      cost: PropTypes.number,
    }),
    tts: PropTypes.shape({
      cost: PropTypes.number,
    }),
  }),
};

export default TestDetailCostBreakdown;
