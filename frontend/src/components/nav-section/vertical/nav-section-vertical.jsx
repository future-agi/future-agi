import React from "react";
import PropTypes from "prop-types";
import { memo, useState, useCallback } from "react";

import Stack from "@mui/material/Stack";
import ListSubheader from "@mui/material/ListSubheader";
import Box from "@mui/material/Box";

import NavList from "./nav-list";
import { Divider, Typography } from "@mui/material";

// ----------------------------------------------------------------------

function NavSectionVertical({
  data,
  slotProps,
  isBottomSection,
  label = "Main",
  ...other
}) {
  return (
    <Stack
      component="nav"
      id="nav-section-vertical"
      aria-label={label}
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
  // The landmark's name; every nav on a page needs a different one.
  label: PropTypes.string,
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

  return (
    <Stack>
      {subheader ? (
        <>
          <ListSubheader
            component="div"
            disableGutters
            disableSticky
            onClick={handleToggle}
            sx={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 0.5,
              cursor: "pointer",
              typography: "overline",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              color: "text.secondary",
              mt: 1,
              p: (theme) => theme.spacing(0.25, 0),
              textTransform: "uppercase",
              transition: (theme) =>
                theme.transitions.create(["color"], {
                  duration: theme.transitions.duration.shortest,
                }),
              "&:hover": {
                color: "text.primary",
              },
              ...slotProps?.subheader,
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                paddingX: 1.5,
                width: "100%", // Take full width
              }}
            >
              <Typography
                fontSize="12px"
                textTransform={"capitalize"}
                fontWeight={500}
                color={"text.secondary"}
              >
                {subheader
                  .toLowerCase()
                  .replace(/\b\w/g, (char) => char.toUpperCase())}
              </Typography>
              <Divider
                sx={{
                  borderColor: "divider",
                  borderBottomWidth: "1px",
                  flexGrow: 1,
                  mt: 0.2,
                }}
              />
            </Box>
          </ListSubheader>

          <Box sx={{ px: 0.5, mb: "10px", width: "100%" }}>{renderContent}</Box>
        </>
      ) : (
        <Box sx={{ px: 0.5, mb: "10px" }}>{renderContent}</Box>
      )}
    </Stack>
  );
}

Group.propTypes = {
  items: PropTypes.array,
  subheader: PropTypes.string,
  slotProps: PropTypes.object,
};
