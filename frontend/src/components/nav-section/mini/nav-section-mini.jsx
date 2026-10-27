import React from "react";
import { memo } from "react";
import PropTypes from "prop-types";

import Stack from "@mui/material/Stack";

import NavList from "./nav-list";
import { Divider } from "@mui/material";

// ----------------------------------------------------------------------

function NavSectionMini({ data, slotProps, ...other }) {
  return (
    <Stack component="nav" id="nav-section-mini" {...other}>
      {data.map((group, index) => (
        <React.Fragment key={group.subheader || index}>
          <Group items={group.items} slotProps={slotProps} />
          {index !== data.length - 1 && (
            <Divider
              orientation="horizontal"
              sx={{
                width: "28px",
                height: "1px",
                mx: "auto",
                backgroundColor: "divider",
                opacity: 1,
                mt: "8px",
                mb: "8px",
              }}
            />
          )}
        </React.Fragment>
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
