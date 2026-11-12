import {
  formatExactMoney,
  isMoneyValue,
  moneySign,
  roundMoneyPercent,
  sumMoneyValues,
} from "src/sections/common/format/money";
import { Box, Typography, useTheme, Checkbox, Button } from "@mui/material";
import { chartAxisColors } from "src/theme/data-colors";
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

    return costBreakdownOrder.map((curr) => ({
      label: curr.label,
      value: curr.value,
      cost: costBreakdown?.[curr.value]?.cost,
      color: colorMap[curr.value],
    }));
  }, [costBreakdown, theme.palette.blue]);

  const allVisibleItems = () =>
    allData.reduce((acc, item) => ({ ...acc, [item.value]: true }), {});
  const [visibleItems, setVisibleItems] = useState(allVisibleItems);
  const restoreItems = () => setVisibleItems(allVisibleItems());

  // Numeric coordinates are decoration only. Use the same plottable population
  // for drawing and the last-visible guard, not checked zero/unknown rows.
  const plottableData = allData.filter((item) => {
    const coordinate = Number(item.cost);
    return (
      moneySign(item.cost) === 1 &&
      Number.isFinite(coordinate) &&
      coordinate > 0
    );
  });
  const plottedData = plottableData.filter((item) => visibleItems[item.value]);
  const series = plottedData.map((item) => Number(item.cost));
  const labels = plottedData.map((item) => item.label);
  const colors = plottedData.map((item) => item.color);
  const hasHiddenItems = allData.some((item) => !visibleItems[item.value]);

  const toggleItem = (value) => {
    setVisibleItems((prev) => {
      const visiblePlottable = plottableData.filter((item) => prev[item.value]);
      if (
        visiblePlottable.length === 1 &&
        visiblePlottable[0].value === value
      ) {
        return allVisibleItems();
      }
      return { ...prev, [value]: !prev[value] };
    });
  };

  const exactSeries = plottedData.map((item) => item.cost);
  // Hiding an unpriced component must never turn a partial total into a known
  // total. Zero-valued components still belong to the priced population.
  const hasUnknownCost = allData.some((item) => !isMoneyValue(item.cost));
  const totalCost = hasUnknownCost
    ? null
    : sumMoneyValues(
        allData
          // With no chart, show the full call total rather than a selection of
          // zero companions that conceals a hidden paid component after refresh.
          .filter((item) => !plottedData.length || visibleItems[item.value])
          .map((item) => item.cost),
      );
  const shareText = (cost) => {
    const percentage = roundMoneyPercent(cost, totalCost);
    return percentage == null ? "—" : `${percentage}%`;
  };
  const allCostsZero = allData.every((item) => moneySign(item.cost) === 0);

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
              fontSize: "16px",
              fontFamily: "IBM Plex Sans",
              formatter: (val, opts) => {
                return opts?.seriesIndex != null
                  ? formatExactMoney(exactSeries[opts.seriesIndex])
                  : `≈ ${formatExactMoney(val)}`;
              },
            },
            total: {
              show: true,
              showAlways: true,
              label: "Total Cost",
              fontWeight: 600,
              fontSize: "13px",
              fontFamily: "IBM Plex Sans",
              color: "var(--text-secondary)",
              formatter: () => {
                return formatExactMoney(totalCost);
              },
            },
          },
        },
      },
    },
    dataLabels: {
      enabled: true,
      formatter: (val, opts) => {
        const actualValue = exactSeries[opts.seriesIndex];
        return [formatExactMoney(actualValue), shareText(actualValue)];
      },
      style: {
        fontSize: "13px",
        fontFamily: "IBM Plex Sans",
        fontWeight: 600,
        colors: [chartAxisColors(theme).surface],
      },
      dropShadow: {
        enabled: false,
      },
    },
    tooltip: {
      enabled: true,
      custom: ({ seriesIndex, w }) => {
        const label = w.globals.labels[seriesIndex];
        const value = exactSeries[seriesIndex];
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
              ${formatExactMoney(value)}
            </div>
          </div>
        `;
      },
    },
  };

  const isCostPreset = plottedData.length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography typography="s1_2" fontWeight="fontWeightMedium">
        Cost Breakdown (Call cost distribution)
      </Typography>
      <ShowComponent condition={isCostPreset || hasHiddenItems}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <ShowComponent condition={isCostPreset}>
            <Box sx={{ flex: "1 1 0", minWidth: 0 }}>
              <ApexCharts
                options={pieOptions}
                series={series}
                type="donut"
                height="280px"
              />
            </Box>
          </ShowComponent>
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
              const percentage = shareText(
                visibleItems[item.value] ? item.cost : "0",
              );

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
                    inputProps={{ "aria-label": `Show ${item.label} cost` }}
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
                      borderRadius: "4px",
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
                      {formatExactMoney(item.cost)} ({percentage})
                    </Typography>
                  </Box>
                </Box>
              );
            })}
            <ShowComponent condition={hasHiddenItems}>
              <Button size="small" onClick={restoreItems}>
                Restore all components
              </Button>
            </ShowComponent>
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
            {allCostsZero
              ? "All costs are $0 for this call"
              : hasUnknownCost
                ? "Cost data is unavailable for this call"
                : `Total cost: ${formatExactMoney(totalCost)} (chart unavailable)`}
          </Typography>
        </Box>
      </ShowComponent>
      <Box
        sx={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 1 }}
      >
        <CostCard
          title="Speech-to-text cost"
          icon="/assets/icons/simulate/ic_speach_to_text.svg"
          iconColor="status.warning.fg"
          iconBgColor="status.warning.bg"
          value={costBreakdown?.stt?.cost}
        />
        <CostCard
          title="LLM cost"
          icon="/assets/icons/simulate/ic_llm.svg"
          iconColor="primary.main"
          iconBgColor="action.hover"
          value={costBreakdown?.llm?.cost}
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
          iconColor="status.info.fg"
          iconBgColor="status.info.bg"
          value={costBreakdown?.tts?.cost}
        />
      </Box>
    </Box>
  );
};

TestDetailCostBreakdown.propTypes = {
  costBreakdown: PropTypes.shape({
    llm: PropTypes.shape({
      cost: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
      promptTokens: PropTypes.number,
      completionTokens: PropTypes.number,
    }),
    stt: PropTypes.shape({
      cost: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    }),
    tts: PropTypes.shape({
      cost: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    }),
  }),
};

export default TestDetailCostBreakdown;
