import { Button, Typography } from "@mui/material";
import Box from "@mui/material/Box/Box";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useAuthContext } from "src/auth/hooks";
import SvgColor from "src/components/svg-color";
import { menuesConstant } from "./../constant";

const InitialSetup = ({ currentLabel, setCurrentLabel, data }) => {
  const { menusList, keySelector, statusColor } = menuesConstant;
  const { user } = useAuthContext();

  const menus = useMemo(() => {
    const menu = JSON.parse(JSON.stringify(menusList));
    return [...menu].map((item) => {
      if (item.label === currentLabel) {
        item.status = "active";
      } else if (data?.[keySelector[item.label]]) {
        item.status = "complete";
        item.icon = "/assets/icons/navbar/ic_evaluate.svg";
      } else {
        item.status = "incomplete";
      }
      return item;
    });
  }, [currentLabel, data, keySelector, menusList]);

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
            const color = statusColor[item.status || "incomplete"];
            return (
              <Button
                key={index}
                fullWidth
                sx={{
                  display: "flex",
                  px: "12px",
                  flexDirection: "row",
                  justifyContent: "flex-start",
                  border: `1px solid`,
                  borderColor: color.borderColor,
                  bgcolor: color.backgroundColor,
                  borderRadius: "4px",
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
                      src={item.icon}
                      sx={{
                        color: color.iconColor,
                        height: "16px",
                        width: "16px",
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
