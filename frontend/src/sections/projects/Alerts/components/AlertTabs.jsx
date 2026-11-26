import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getUniqueColorPalette } from "src/utils/utils";
import SvgColor from "../../../../components/svg-color/svg-color";

function AlertTab({ selected, onClick, index, title, completed }) {
  const theme = useTheme();
  const { tagBackground, tagForeground } = getUniqueColorPalette(index);
  return (
    <Button
      onClick={onClick}
      sx={{
        border: "1px solid",
        borderColor: completed
          ? "status.success.solid"
          : selected
            ? "primary.main"
            : "divider",
        width: "100%",
        display: "flex",
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "flex-start",
      }}
    >
      <Stack flexDirection={"row"} alignItems={"center"} gap={1}>
        <Box
          sx={{
            height: theme.spacing(3.5),
            width: theme.spacing(3.5),
            backgroundColor: completed ? "status.success.bg" : tagBackground,
            ...(completed && {
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
            }),
          }}
        >
          {completed ? (
            <SvgColor
              src="/assets/icons/status/success.svg"
              sx={{
                height: 15,
                width: 15,
                bgcolor: "status.success.fg",
              }}
            />
          ) : (
            <Typography
              color={tagForeground}
              variant="s3"
              fontWeight={"fontWeightSemiBold"}
            >
              {index + 1}
            </Typography>
          )}
        </Box>
        <Typography
          variant="s1"
          color={"text.primary"}
          fontWeight={selected ? "fontWeightMedium" : "fontWeightRegular"}
        >
          {title}
        </Typography>
      </Stack>
    </Button>
  );
}

AlertTab.propTypes = {
  selected: PropTypes.bool,
  onClick: PropTypes.func,
  index: PropTypes.number,
  title: PropTypes.string,
  completed: PropTypes.bool,
};

export default function AlertTabs({
  tabs = [],
  currentTab,
  onChange,
  completedIndexes = [],
}) {
  return (
    <Stack flexDirection={"row"} gap={2}>
      {tabs?.map((tab, index) => (
        <AlertTab
          key={index}
          title={tab.title}
          index={index}
          onClick={() => onChange(index)}
          selected={index === currentTab}
          completed={completedIndexes.includes(index)}
        />
      ))}
    </Stack>
  );
}

AlertTabs.displayName = "AlertTabs";

AlertTabs.propTypes = {
  tabs: PropTypes.array,
  currentTab: PropTypes.number,
  onChange: PropTypes.func,
  completedIndexes: PropTypes.array,
};
