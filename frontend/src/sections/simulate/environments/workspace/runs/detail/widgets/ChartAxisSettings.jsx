import PropTypes from "prop-types";
import { Box, Divider, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  AxisSection,
  ToggleButtons,
} from "src/sections/dashboards/WidgetEditorView";
import {
  AXIS_LABEL_MAX_LENGTH,
  LETTER_LABELS,
} from "src/sections/dashboards/widgetEditorConstants";
import { DEFAULT_DECIMALS } from "src/sections/dashboards/widgetUtils";

// The same axis defaults the dashboards editor resets to.
export const defaultLeftYAxis = () => ({
  visible: true,
  label: "",
  unit: "",
  prefixSuffix: "prefix",
  abbreviation: true,
  decimals: DEFAULT_DECIMALS,
  min: "",
  max: "",
  outOfBounds: "visible",
  scale: "linear",
});

export const defaultRightYAxis = () => ({
  ...defaultLeftYAxis(),
  visible: false,
  outOfBounds: "hidden",
});

export const defaultAxisConfig = () => ({
  leftY: defaultLeftYAxis(),
  rightY: defaultRightYAxis(),
  xAxis: { visible: true, label: "" },
  // { [seriesIndex]: "left" | "right" }
  seriesAxis: {},
});

const NO_AXES = {
  pie: "Pie charts do not have axis settings",
  table: "Table view does not have axis settings",
  metric: "Metric cards do not have axis settings",
};

/**
 * The dashboards editor's Chart tab: which axis each series sits on, the left
 * and right Y-axis settings and the X-axis, built from the same parts.
 */
export default function ChartAxisSettings({
  chartKind,
  series,
  colorFor,
  axisConfig,
  onUpdateAxis,
  onSetSeriesAxis,
  onResetAxis,
  theme,
}) {
  if (NO_AXES[chartKind]) {
    return (
      <Typography
        typography="s1"
        sx={{
          color: "text.secondary",
          fontStyle: "italic",
          textAlign: "center",
          mt: 4,
        }}
      >
        {NO_AXES[chartKind]}
      </Typography>
    );
  }
  return (
    <>
      <Typography
        variant="overline"
        fontWeight={700}
        sx={{ mb: 2, display: "block", letterSpacing: 1.5 }}
      >
        AXIS
      </Typography>
      <Box>
        <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
          Axis Assignment
        </Typography>
        {series.map((item, index) => {
          const color = colorFor(item.name);
          return (
            <Stack
              key={item.key}
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ mb: 1 }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                sx={{ flex: 1, minWidth: 0 }}
              >
                <Box
                  sx={{
                    width: 22,
                    height: 22,
                    borderRadius: 0.5,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    bgcolor: `${color}22`,
                    color,
                    typography: "s3",
                    fontWeight: 700,
                  }}
                >
                  {LETTER_LABELS[index] || index}
                </Box>
                <Iconify
                  icon="mdi:chart-line"
                  width={16}
                  sx={{ color, flexShrink: 0 }}
                />
                <Typography typography="s1" noWrap sx={{ fontWeight: 500 }}>
                  {item.name?.split(" (")[0] || item.name}
                </Typography>
              </Stack>
              <ToggleButtons
                options={[
                  { label: "L", value: "left" },
                  { label: "R", value: "right" },
                ]}
                value={axisConfig.seriesAxis[index] || "left"}
                onChange={(side) => onSetSeriesAxis(index, side)}
                theme={theme}
              />
            </Stack>
          );
        })}
        {series.length === 0 && (
          <Typography
            typography="s1"
            sx={{ color: "text.secondary", fontStyle: "italic" }}
          >
            Add metrics to see axis assignments
          </Typography>
        )}
      </Box>
      <Divider sx={{ my: 2 }} />
      <AxisSection
        title="Left Y-Axis"
        config={axisConfig.leftY}
        onChange={(key, value) => onUpdateAxis("leftY", key, value)}
        theme={theme}
        showReset
        onReset={() => onResetAxis("leftY")}
      />
      <Divider sx={{ my: 2 }} />
      <AxisSection
        title="Right Y-Axis"
        config={axisConfig.rightY}
        onChange={(key, value) => onUpdateAxis("rightY", key, value)}
        theme={theme}
        showReset
        onReset={() => onResetAxis("rightY")}
      />
      <Divider sx={{ my: 2 }} />
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
          X-Axis
        </Typography>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 1.5 }}
        >
          <Typography typography="s1" sx={{ color: "text.secondary" }}>
            Axis
          </Typography>
          <ToggleButtons
            options={[
              { label: "Visible", value: true },
              { label: "Hidden", value: false },
            ]}
            value={axisConfig.xAxis.visible}
            onChange={(value) => onUpdateAxis("xAxis", "visible", value)}
            theme={theme}
          />
        </Stack>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography typography="s1" sx={{ color: "text.secondary" }}>
            Label
          </Typography>
          <TextField
            size="small"
            value={axisConfig.xAxis.label}
            onChange={(event) =>
              onUpdateAxis("xAxis", "label", event.target.value)
            }
            placeholder="e.g. Calls"
            inputProps={{ maxLength: AXIS_LABEL_MAX_LENGTH }}
            sx={{
              width: 180,
              "& .MuiOutlinedInput-root": { typography: "s1" },
            }}
          />
        </Stack>
      </Box>
    </>
  );
}

ChartAxisSettings.propTypes = {
  chartKind: PropTypes.oneOf(["pie", "table", "metric", null]),
  series: PropTypes.array.isRequired,
  colorFor: PropTypes.func.isRequired,
  axisConfig: PropTypes.object.isRequired,
  onUpdateAxis: PropTypes.func.isRequired,
  onSetSeriesAxis: PropTypes.func.isRequired,
  onResetAxis: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};
