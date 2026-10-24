import { Button, Typography, useTheme } from "@mui/material";
import Box from "@mui/material/Box/Box";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useAuthContext } from "src/auth/hooks";
import SvgColor from "src/components/svg-color";
import { menuesConstant } from "./../constant";
import { statusColors } from "src/utils/runStatus";
import { useIntegrations } from "src/sections/simulate-v2/_mock/integrations";

// Each setup step's state, as a role in the one status map.
const STEP_ROLE = {
  complete: "success",
  active: "info",
  incomplete: "neutral",
};

const stepColors = (theme, status) => {
  const role = STEP_ROLE[status] || "neutral";
  if (role === "neutral") {
    return {
      iconColor: theme.palette.text.primary,
      titleColor: theme.palette.text.primary,
      iconBackgroundColor: theme.palette.background.neutral,
      hoverIconBackgroundColor: theme.palette.background.neutral,
      borderColor: theme.palette.divider,
      hoverColor: "",
      backgroundColor: "",
    };
  }
  const c = statusColors(theme, role);
  return {
    iconColor: c.fg,
    titleColor: theme.palette.text.primary,
    iconBackgroundColor: c.bg,
    hoverIconBackgroundColor: c.bg,
    borderColor: c.solid,
    hoverColor: role === "success" ? c.bg : "",
    backgroundColor: "",
  };
};

const InitialSetup = ({ currentLabel, setCurrentLabel, data }) => {
  const theme = useTheme();
  const { menusList, keySelector } = menuesConstant;
  const { user } = useAuthContext();
  /* Workspace tools are prototype-backed — complete once both are connected. */
  const { github, slack } = useIntegrations();
  const status = useMemo(
    () => ({ ...(data || {}), tools: !!(github && slack) }),
    [data, github, slack],
  );

  const menus = useMemo(() => {
    const menu = JSON.parse(JSON.stringify(menusList));
    return [...menu].map((item) => {
      if (item.label === currentLabel) {
        item.status = "active";
      } else if (status?.[keySelector[item.label]]) {
        item.status = "complete";
        item.icon = "/assets/icons/navbar/ic_evaluate.svg";
      } else {
        item.status = "incomplete";
      }
      return item;
    });
  }, [currentLabel, status, keySelector, menusList]);

  const handleMenuClick = (selected) => {
    setCurrentLabel(selected.label);
  };

  return (
    <Box p={"16px"}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          minWidth: "270px",
          maxWidth: "270px",
        }}
      >
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color={"text.primary"}
        >
          Initial Setup
        </Typography>
        <Typography
          variant="s2"
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          Help you guide through the key features and functionalities
        </Typography>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: "8px",
          marginTop: "16px",
          // minWidth: "max-content",
          // maxWidth: "max-content",
        }}
      >
        {menus
          .filter((item) => {
            return (user?.organization_role ?? user?.organizationRole) ===
              "Owner"
              ? true
              : item.label !== "inviteTeamMembers";
          })
          ?.map((item, index) => {
            const color = stepColors(theme, item.status || "incomplete");
            return (
              <Button
                key={index}
                fullWidth
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  justifyContent: "flex-start",
                  border: `1px solid`,
                  borderColor: color.borderColor,
                  bgcolor: color.backgroundColor,
                  "&:hover": {
                    bgcolor: color.hoverColor,
                    "& span div": {
                      bgcolor: `${color.hoverIconBackgroundColor} !important`,
                    },
                  },
                }}
                onClick={() => handleMenuClick(item)}
                startIcon={
                  <Box
                    bgcolor={color.iconBackgroundColor}
                    height={"28px"}
                    width={"28px"}
                    borderRadius={"2px"}
                    pb={"12px"}
                  >
                    <SvgColor
                      width={16}
                      src={item.icon}
                      sx={{
                        color: color.iconColor,
                        mb: "2px",
                      }}
                    />
                  </Box>
                }
              >
                <Typography
                  variant="s2"
                  fontWeight={
                    item.status === "active"
                      ? "fontWeightSemiBold"
                      : "fontWeightRegular"
                  }
                  color={"text.primary"}
                >
                  {item.title}
                </Typography>
              </Button>
            );
          })}
      </Box>
    </Box>
  );
};

export default InitialSetup;

InitialSetup.propTypes = {
  setCurrentLabel: PropTypes.func,
  currentLabel: PropTypes.string,
  setShowDemoModal: PropTypes.func,
  data: PropTypes.object,
};
