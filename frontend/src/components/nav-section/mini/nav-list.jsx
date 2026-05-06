import React from "react";
import PropTypes from "prop-types";
import { useRef, useState, useEffect, useCallback } from "react";

import Stack from "@mui/material/Stack";
import Popover from "@mui/material/Popover";
import { alpha, useTheme } from "@mui/material/styles";

import { usePathname } from "src/routes/hooks";
import { useActiveLink } from "src/routes/hooks/use-active-link";

import NavItem from "./nav-item";

// ----------------------------------------------------------------------

export default function NavList({ data, depth, slotProps }) {
  const theme = useTheme();
  const navRef = useRef(null);

  const pathname = usePathname();

  const active = useActiveLink(
    data.path,
    !!data.children || !!data?.hasChildren,
  );

  const [openMenu, setOpenMenu] = useState(false);

  const handleCloseMenu = useCallback(() => {
    setOpenMenu(false);
  }, []);

  useEffect(() => {
    if (openMenu) {
      handleCloseMenu();
    }
  }, [handleCloseMenu, openMenu, pathname]);

  const handleOpenMenu = useCallback(() => {
    if (data.children) {
      setOpenMenu(true);
    }
  }, [data.children]);

  return (
    <>
      <NavItem
        ref={navRef}
        open={openMenu}
        onMouseEnter={handleOpenMenu}
        onMouseLeave={handleCloseMenu}
        //
        title={data.title}
        path={data.path}
        icon={data.icon}
        info={data.info}
        roles={data.roles}
        caption={data.caption}
        disabled={data.disabled}
        disabledTooltip={data.disabledTooltip}
        //
        depth={depth}
        hasChild={!!data.children}
        externalLink={data.path?.includes("http")}
        currentRole={slotProps?.currentRole}
        //
        active={active}
        className={active ? "active" : ""}
        sx={{
          mb: `${slotProps?.gap}px`,
          bgcolor: active
            ? alpha(theme.palette.primary.main, 0.1)
            : "transparent",
          borderRadius: "8px",
          color: active ? "primary.main" : "text.secondary",
          "&:hover": {
            bgcolor: active
              ? alpha(theme.palette.primary.main, 0.1)
              : "background.neutral",
          },
          ...(depth === 1 ? slotProps?.rootItem : slotProps?.subItem),
        }}
        eventTrigger={data?.eventTrigger}
      />

      {!!data.children && (
        <Popover
          disableScrollLock
          open={openMenu}
          anchorEl={navRef.current}
          anchorOrigin={{ vertical: "center", horizontal: "right" }}
          transformOrigin={{ vertical: "center", horizontal: "left" }}
          slotProps={{
            paper: {
              onMouseEnter: handleOpenMenu,
              onMouseLeave: handleCloseMenu,
              sx: {
                mt: 0.5,
                minWidth: 160,
                ...(openMenu && {
                  pointerEvents: "auto",
                }),
              },
            },
          }}
          sx={{
            pointerEvents: "none",
          }}
        >
          <NavSubList
            data={data.children}
            depth={depth}
            slotProps={slotProps}
          />
        </Popover>
      )}
    </>
  );
}

NavList.propTypes = {
  data: PropTypes.object,
  depth: PropTypes.number,
  slotProps: PropTypes.object,
};

// ----------------------------------------------------------------------

function NavSubList({ data, depth, slotProps }) {
  return (
    <Stack spacing={0.5} sx={{ paddingLeft: 0, marginLeft: 0 }}>
      {data.map((list) => (
        <NavList
          key={list.title}
          data={list}
          depth={depth + 1}
          slotProps={slotProps}
        />
      ))}
    </Stack>
  );
}

NavSubList.propTypes = {
  data: PropTypes.array,
  depth: PropTypes.number,
  slotProps: PropTypes.object,
};
