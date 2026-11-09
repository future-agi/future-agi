import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import ListSubheader from "@mui/material/ListSubheader";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { usePathname } from "src/routes/hooks";
import NavItem from "src/components/nav-section/vertical/nav-item";
import MiniNavItem from "src/components/nav-section/mini/nav-item";

import { activeRailPath } from "./gateway-rail";
import { gatewayNavIcon } from "./gateway-icons";

// The AI Gateway's rail, drawn with the Future AGI nav's own items (NavItem
// styles the active state itself) and its group-header look, so it reads as
// the same sidebar. It marks one item active: the
// longest one the URL is under (the FA nav marks every prefix, which would
// keep Overview lit on every gateway page).
//
// It is meant to fit a 1440x960 screen without scrolling: Billing
// & credits lives in Settings,
// and the rail keeps the FA nav's gap between groups (label 8 px above, group
// 10 px below) but drops what only adds height: its labels' 2 px padding
// above and below, and the margin after the last group. The fit is computed,
// not browser-measured: about 711 px of rail in the 733 px box, from the
// measured 782 px less the removed heights, with Integrations and MCP tools
// in the Providers group rather than a group of their own
// (GatewayRail.fit.test.jsx pins the parts; jsdom lays nothing out). The
// browser pass is still owed.

function GroupHeader({ title }) {
  return (
    <ListSubheader
      disableGutters
      disableSticky
      component="div"
      sx={{
        mt: 1,
        p: 0,
        bgcolor: "transparent",
        lineHeight: "normal",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          width: "100%",
        }}
      >
        <Typography
          component="span"
          fontSize="12px"
          fontWeight={500}
          color="text.secondary"
          noWrap
        >
          {title}
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
  );
}

GroupHeader.propTypes = { title: PropTypes.string.isRequired };

export default function GatewayRail({ groups, collapsed = false }) {
  const pathname = usePathname();
  const active = useMemo(
    () =>
      activeRailPath(
        pathname,
        groups.flatMap((group) => group.items),
      ),
    [groups, pathname],
  );

  if (collapsed) {
    return (
      <Stack
        component="nav"
        aria-label="AI Gateway"
        data-testid="gateway-rail"
        alignItems="center"
        gap="2px"
      >
        {groups.map((group, index) => (
          <React.Fragment key={group.id}>
            {index > 0 && (
              <Divider
                sx={{ width: 20, my: 0.5, borderColor: "action.hover" }}
              />
            )}
            {group.items.map((item) => {
              const isActive = item.path === active;
              return (
                <MiniNavItem
                  key={item.path}
                  title={item.title}
                  path={item.path}
                  icon={gatewayNavIcon(item.icon)}
                  depth={1}
                  active={isActive}
                  className={isActive ? "active" : ""}
                  aria-current={isActive ? "page" : undefined}
                />
              );
            })}
          </React.Fragment>
        ))}
      </Stack>
    );
  }

  return (
    <Stack component="nav" aria-label="AI Gateway" data-testid="gateway-rail">
      {groups.map((group, index) => (
        <Stack key={group.id} role="group" aria-label={group.subheader}>
          {group.subheader && <GroupHeader title={group.subheader} />}
          <Box
            sx={{
              px: 0.5,
              mb: index === groups.length - 1 ? 0 : "10px",
              width: "100%",
            }}
          >
            {group.items.map((item) => {
              const isActive = item.path === active;
              return (
                <NavItem
                  key={item.path}
                  title={
                    <Box
                      component="span"
                      sx={{
                        fontSize: "13px",
                        color: isActive ? "primary.main" : "text.secondary",
                        fontWeight: isActive ? 600 : 500,
                      }}
                    >
                      {item.title}
                    </Box>
                  }
                  path={item.path}
                  icon={gatewayNavIcon(item.icon)}
                  depth={1}
                  active={isActive}
                  className={isActive ? "active" : ""}
                  aria-current={isActive ? "page" : undefined}
                />
              );
            })}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}

GatewayRail.propTypes = {
  groups: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      subheader: PropTypes.string,
      items: PropTypes.arrayOf(
        PropTypes.shape({
          title: PropTypes.string.isRequired,
          path: PropTypes.string.isRequired,
          icon: PropTypes.string.isRequired,
        }),
      ).isRequired,
    }),
  ).isRequired,
  collapsed: PropTypes.bool,
};
