import React from "react";
import PropTypes from "prop-types";
import { useState, useEffect, useCallback } from "react";

import Collapse from "@mui/material/Collapse";

import { usePathname } from "src/routes/hooks";
import { useActiveLink } from "src/routes/hooks/use-active-link";

import NavItem from "./nav-item";
import { Box } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Label from "src/components/label";
// ----------------------------------------------------------------------

export default function NavList({ data, depth, slotProps }) {
  const theme = useTheme();
  const pathname = usePathname();

  const active = useActiveLink(
    data.path,
    !!data.children || !!data.hasChildren,
  );

  const [openMenu, setOpenMenu] = useState(active);

  const handleCloseMenu = useCallback(() => {
    setOpenMenu(false);
  }, []);

  useEffect(() => {
    if (!active) {
      handleCloseMenu();
    }
  }, [active, handleCloseMenu, pathname]);

  const handleToggleMenu = useCallback(() => {
    if (data.children) {
      setOpenMenu((prev) => !prev);
    }
  }, [data.children]);

  return (
    <>
      <NavItem
        open={openMenu}
        onClick={handleToggleMenu}
        //
        title={
          <>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                fontSize: "13px",
                color: active ? "primary.main" : "text.secondary",
                fontWeight: active ? 600 : 500,
              }}
            >
              {data.title}
              {data?.labelText && (
                <Box>
                  <Label color="primary">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="10px"
                      height="10px"
                      viewBox="0 0 10 10"
                      version="1.1"
                    >
                      <g id="surface1">
                        <path
                          fill="var(--primary-main)"
                          d="M 7.167969 0.378906 L 8.882812 0.378906 L 7.84375 5.046875 C 7.640625 5.933594 7.503906 6.476562 7.429688 6.679688 C 7.699219 7.914062 8.089844 8.535156 8.597656 8.535156 C 9.203125 8.535156 9.515625 8.117188 9.539062 7.285156 L 9.898438 7.285156 C 9.882812 8.0625 9.738281 8.695312 9.464844 9.179688 C 9.203125 9.65625 8.859375 9.894531 8.429688 9.894531 C 8.070312 9.894531 7.804688 9.75 7.632812 9.460938 C 7.457031 9.171875 7.269531 8.582031 7.066406 7.6875 C 6.367188 9.160156 5.226562 9.894531 3.640625 9.894531 C 2.558594 9.894531 1.695312 9.476562 1.058594 8.632812 C 0.417969 7.789062 0.101562 6.578125 0.101562 5.003906 C 0.101562 3.460938 0.449219 2.253906 1.148438 1.394531 C 1.847656 0.535156 2.6875 0.105469 3.667969 0.105469 C 4.363281 0.105469 4.929688 0.351562 5.375 0.839844 C 5.816406 1.332031 6.210938 2.132812 6.554688 3.242188 Z M 6.28125 4.328125 C 5.964844 3.246094 5.613281 2.402344 5.222656 1.796875 C 4.832031 1.1875 4.363281 0.882812 3.820312 0.882812 C 2.589844 0.882812 1.976562 2.320312 1.976562 5.195312 C 1.976562 7.925781 2.5625 9.289062 3.738281 9.289062 C 4.761719 9.289062 5.515625 8.085938 6.007812 5.679688 Z M 6.28125 4.328125 "
                        />
                      </g>
                    </svg>
                  </Label>
                </Box>
              )}
            </Box>
          </>
        }
        titleIcon={data.titleIcon}
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
          mb: "2px",
          minHeight: 32,
          borderRadius: 1,
          bgcolor: active
            ? alpha(theme.palette.primary.main, 0.1)
            : "transparent",
          color: active ? "primary.main" : "text.primary",
          width: "100%",
          fontWeight: active ? 600 : 500,
          "&:hover": {
            bgcolor: active
              ? alpha(theme.palette.primary.main, 0.1)
              : "action.hover",
          },
          ...slotProps?.rootItem,
        }}
        //
        eventTrigger={data.eventTrigger}
      />

      {data.children && (
        <Collapse in={openMenu}>
          <Box sx={{ pl: 3 }}>
            {data.children.map((list) => (
              <NavList
                key={list.title + list.path}
                data={list}
                depth={depth + 1}
                slotProps={slotProps}
              />
            ))}
          </Box>
        </Collapse>
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
    <>
      {data.map((list) => (
        <NavList
          key={list.title}
          data={list}
          depth={depth + 1}
          slotProps={slotProps}
        />
      ))}
    </>
  );
}

NavSubList.propTypes = {
  data: PropTypes.array,
  depth: PropTypes.number,
  slotProps: PropTypes.object,
};
