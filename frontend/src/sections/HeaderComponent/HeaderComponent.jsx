import { Box, Button, styled } from "@mui/material";
import React from "react";
import CustomBreadcrumbs from "src/components/custom-breadcrumbs";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useLocation, useNavigate } from "react-router";
import logger from "src/utils/logger";

const BackButton = styled(Button)(({ theme }) => ({
  backgroundColor: theme.palette.divider,
  border: `2px solid ${theme.palette.divider}`,
  height: "100%",
  color: theme.palette.primary.main,
  paddingRight: "14px",
}));

export const HeaderComponent = ({ links, routeDepth }) => {
  const navigate = useNavigate();

  const location = useLocation();
  // Function to get the parent path
  const getParentPath = (path) => {
    const pathArray = path.split("/");
    pathArray.pop();
    return pathArray.join("/");
  };

  // Check if the current route is nested (has more than one segment)
  const isNestedRoute =
    location.pathname.split("/").filter(Boolean).length > routeDepth;

  const handleGoBack = () => {
    if (isNestedRoute) {
      logger.debug("Parent Path Name", getParentPath(location.pathname));
      navigate(getParentPath(location.pathname));
    }
  };

  // const findMainParentRoute = (path) => {

  // const handleGoBack = () => {
  //   const parentPath = findNearestParentRoute(
  //     dashboardRoutes,
  //     location.pathname,
  //   );
  //   if (parentPath) {
  //     navigate(parentPath);
  //   } else {
  //     // If no parent defined, just go up one level
  //     navigate("..");
  //   }
  // };

  return (
    <Box
      sx={{
        paddingTop: "15px",
        paddingX: "20px",
        paddingBottom: "12px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        position: "sticky",
        top: 0,
        zIndex: 1,
        backgroundColor: "background.default",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: "16px" }}>
        {isNestedRoute && (
          <BackButton
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width="24px" />
            }
            size="small"
            onClick={handleGoBack}
          >
            Back
          </BackButton>
        )}
        <CustomBreadcrumbs links={links} />
      </Box>
      {/* <Popover
        anchorEl={dropDownRef.current}
        open={isOpen}
        onClose={() => setOpen(false)}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        PaperProps={{
          style: {
            backgroundColor: "transparent",
            boxShadow: "none",
            padding: 0,
          },
        }}
      >
        <Box
          sx={{
            position: "relative",
            mt: "10px",
            "&::before": {
              backgroundColor: "background.paper",
              content: '""',
              display: "block",
              position: "absolute",
              width: 12,
              height: 12,
              top: -6,
              transform: "rotate(45deg)",
              right: 12,
            },
          }}
        />
        <Box
          sx={{
            backgroundColor: "background.paper",
            borderRadius: "12px",
            padding: 1,
            boxShadow:
              "0 0 2px 0 rgba(147, 143, 163, 0.24), -20px 20px 40px -4px rgba(147, 143, 163, 0.24)",
          }}
        >
          <List>
            <ListItemButton
              sx={{ paddingX: 1 }}
              onClick={() => onLogoutClick()}
            >
              <ListItemIcon sx={{ marginRight: 0.5 }}>
                <Iconify
                  icon="solar:double-alt-arrow-right-bold-duotone"
                  size={20}
                  color="error.main"
                />
              </ListItemIcon>
              <ListItemText primary="Logout" />
            </ListItemButton>
          </List>
        </Box>
      </Popover> */}
      {/* <Avatar
        ref={dropDownRef}
        onClick={() => setOpen(true)}
        sx={{ cursor: "pointer" }}
      >
        {user.name[0]}
      </Avatar> */}
    </Box>
  );
};

HeaderComponent.propTypes = {
  links: PropTypes.array,
  routeDepth: PropTypes.number,
};
