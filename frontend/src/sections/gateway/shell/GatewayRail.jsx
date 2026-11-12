import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import ListSubheader from "@mui/material/ListSubheader";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { usePathname } from "src/routes/hooks";
import NavItem from "src/components/nav-section/vertical/nav-item";
import MiniNavItem from "src/components/nav-section/mini/nav-item";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";

import useGatewayCommands from "../hooks/useGatewayCommands";
import { activeRailPath } from "./gateway-rail";
import { GatewayIcon, gatewayNavIcon } from "./gateway-icons";

// The AI Gateway's rail, drawn with the Future AGI nav's own items (NavItem
// styles the active state itself) and its group-header look, so it reads as
// the same sidebar. It marks one item active: the
// longest one the URL is under (the FA nav marks every prefix, which would
// keep Overview lit on every gateway page).
//
// Keep the FA nav's group spacing (label 8 px above, group 10 px below)
// without padding inside labels or after the final group. The finder adds
// a 44 px minimum target and 4 px gap to the earlier ~711 px rail estimate.
// The existing scroller/overflow cue therefore remains necessary; the fit
// spec pins spacing, not a desktop or phone browser measurement.

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
  useGatewayCommands(groups);
  const setPaletteOpen = useShortcutStore((state) => state.setPaletteOpen);
  const openFinder = (event) => {
    // Some browsers do not focus a clicked button. Give the existing dialog
    // a real return target; it owns keyboard selection and focus restoration.
    event.currentTarget.focus();
    setPaletteOpen(true);
  };
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
        <Tooltip title="Find a gateway page" placement="right">
          <IconButton
            aria-label="Find a gateway page"
            aria-haspopup="dialog"
            onClick={openFinder}
            sx={{ width: 44, height: 44, color: "text.secondary" }}
          >
            <GatewayIcon name="search" size={18} />
          </IconButton>
        </Tooltip>
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
                  disabled={item.disabled}
                  disabledTooltip={item.caption}
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
      <Button
        color="inherit"
        aria-haspopup="dialog"
        startIcon={<GatewayIcon name="search" size={18} />}
        onClick={openFinder}
        sx={{
          minHeight: 44,
          mx: 0.5,
          mb: 0.5,
          px: 1,
          justifyContent: "flex-start",
          color: "text.secondary",
          fontSize: "13px",
          fontWeight: 500,
          textTransform: "none",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        Find a gateway page
      </Button>
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
                  disabled={item.disabled}
                  caption={item.caption}
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
          disabled: PropTypes.bool,
          caption: PropTypes.string,
        }),
      ).isRequired,
    }),
  ).isRequired,
  collapsed: PropTypes.bool,
};
