import React from "react";
import { memo } from "react";
import PropTypes from "prop-types";

import Stack from "@mui/material/Stack";

import NavList from "./nav-list";
import Box from "@mui/material/Box";

// ----------------------------------------------------------------------

function NavSectionMini({ data, slotProps, ...other }) {
  /* Design system: the collapsed rail keeps the open sidebar's rhythm —
     24px rows, and each labelled group starts 8px down with a 20px slot
     where the label sits, holding a hairline instead of the text. */
  return (
    <Stack component="nav" id="nav-section-mini" {...other}>
      {data.map((group, index) => (
        <Stack key={group.subheader || index} sx={{ mt: group.subheader ? 1 : 0 }}>
          {group.subheader && (
            <Box
              sx={{
                height: 20,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Box sx={{ width: 20, height: "1px", bgcolor: "divider" }} />
            </Box>
          )}
          <Group items={group.items} slotProps={slotProps} />
        </Stack>
      ))}
    </Stack>
  );
}

NavSectionMini.propTypes = {
  data: PropTypes.array,
  slotProps: PropTypes.object,
};

const MemoizedNavSectionMini = memo(NavSectionMini);
export default MemoizedNavSectionMini;

// ----------------------------------------------------------------------

function Group({ items, slotProps }) {
  return (
    <>
      {items.map((list) => (
        <NavList key={list.title} data={list} depth={1} slotProps={slotProps} />
      ))}
    </>
  );
}

Group.propTypes = {
  items: PropTypes.array,
  slotProps: PropTypes.object,
};
