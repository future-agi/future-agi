import { Box, Divider, Typography } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import CurrentPlanButon from "./CurrentPlanButon";
import PropTypes from "prop-types";

const offers = [
  "Up to 3 team members",
  "3 projects and 10K traces/month",
  "Basic reporting (the essentials)",
  "1GB storage",
  "Community support (you're not alone)",
];

const BasicPricingCard = ({ currentPlan }) => {
  return (
    <Box
      padding={"30px"}
      display="flex"
      position={"relative"}
      gap={2}
      flexDirection={"column"}
      border="1px solid"
      borderColor="divider"
      borderRadius={1}
      sx={{ backgroundColor: "background.paper", width: "calc(33% - 10px)" }}
    >
      <Box>
        <SvgColor
          // @ts-ignore
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{
            width: 24,
            height: 24,
            color: "text.primary",
          }}
        />
        <Box display="flex" gap={1} flexDirection={"column"}>
          <Box display="flex" gap={1}>
            <Typography
              color="text.primary"
              typography="m2"
              fontWeight={"fontWeightSemiBold"}
            >
              Starter Plan
            </Typography>
            {currentPlan && (
              <Box sx={{ top: 0, position: "absolute", right: 0 }}>
                {" "}
                <CurrentPlanButon />
              </Box>
            )}
          </Box>
          <Typography
            color="text.primary"
            typography="s1"
            fontWeight={"fontWeightRegular"}
          >
            Perfect for testing the waters without drowning your budget
          </Typography>
        </Box>
      </Box>
      <Divider orientation="horizontal" />
      <Box display="flex" flexDirection={"column"} gap={1}>
        {offers.map((item, index) => {
          return (
            <Box key={index} display="flex" gap={1}>
              <SvgColor
                // @ts-ignore
                src="/icons/settings/pricing-list.svg"
                sx={{ width: "20px", color: "status.success.fg" }}
              />
              <Typography
                color="text.primary"
                typography="s1"
                fontWeight={"fontWeightRegular"}
              >
                {item}
              </Typography>
            </Box>
          );
        })}
      </Box>
      <Box display="flex" flexDirection={"column"} mt={7}>
        <Typography
          color="text.primary"
          typography="l2"
          fontWeight={"fontWeightSemiBold"}
        >
          $0
        </Typography>
        <Typography
          color="text.disabled"
          typography="s2"
          fontWeight={"fontWeightMedium"}
        >
          forever (seriously)
        </Typography>
      </Box>
    </Box>
  );
};

export default BasicPricingCard;

BasicPricingCard.propTypes = {
  currentPlan: PropTypes.bool,
};
