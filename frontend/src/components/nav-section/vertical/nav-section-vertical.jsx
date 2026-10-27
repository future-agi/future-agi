import React from "react";
import PropTypes from "prop-types";
import { memo, useState, useCallback } from "react";

import Stack from "@mui/material/Stack";
import ListSubheader from "@mui/material/ListSubheader";
import Box from "@mui/material/Box";

import NavList from "./nav-list";

// ----------------------------------------------------------------------

function NavSectionVertical({ data, slotProps, isBottomSection, ...other }) {
  return (
    <Stack
      component="nav"
      id="nav-section-vertical"
      sx={{ pt: isBottomSection ? 0 : 0 }}
      {...other}
    >
      {data.map((group, index) => (
        <Group
          key={group.subheader || index}
          subheader={group.subheader}
          items={group.items}
          slotProps={slotProps}
        />
      ))}
    </Stack>
  );
}

NavSectionVertical.propTypes = {
  data: PropTypes.array,
  slotProps: PropTypes.object,
  isBottomSection: PropTypes.bool,
};

const MemoizedNavSectionVertical = memo(NavSectionVertical);
export default MemoizedNavSectionVertical;

// ----------------------------------------------------------------------

function Group({ subheader, items, slotProps }) {
  const [_open, setOpen] = useState(true);

  const handleToggle = useCallback(() => {
    setOpen((prev) => !prev);
  }, []);

  const renderContent = items.map(
    (list) =>
      (list?.show === true || list?.show === undefined) && (
        <NavList key={list.title} data={list} depth={1} slotProps={slotProps} />
      ),
  );

  // Design system sidebar: sentence-case 11px/500 label in fg.tertiary on a
  // 20px row, no divider rule, 8px between groups, 24px rows with no gap.
  return (
    <Stack sx={{ mt: subheader ? 1 : 0 }}>
      {subheader ? (
        <>
          <ListSubheader
            disableGutters
            disableSticky
            onClick={handleToggle}
            sx={{
              display: "flex",
              alignItems: "center",
              height: 20,
              px: 1,
              mx: 0.5,
              bgcolor: "transparent",
              cursor: "pointer",
              fontSize: 11,
              lineHeight: "16px",
              fontWeight: 500,
              color: "text.subtitle",
              transition: (theme) =>
                theme.transitions.create(["color"], {
                  duration: theme.transitions.duration.shortest,
                }),
              "&:hover": {
                color: "text.secondary",
              },
              ...slotProps?.subheader,
            }}
          >
            {subheader.charAt(0).toUpperCase() +
              subheader.slice(1).toLowerCase()}
          </ListSubheader>

          <Box
            sx={{
              px: 0.5,
              width: "100%",
              display: "flex",
              flexDirection: "column",
              gap: 0,
            }}
          >
            {renderContent}
          </Box>
        </>
      ) : (
        <Box
          sx={{ px: 0.5, display: "flex", flexDirection: "column", gap: 0 }}
        >
          {renderContent}
        </Box>
      )}
    </Stack>
  );
}

Group.propTypes = {
  items: PropTypes.array,
  subheader: PropTypes.string,
  slotProps: PropTypes.object,
};
