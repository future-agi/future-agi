import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FeatureCard from "./FeatureCard";
import { featureCardList } from "../constant";
import ModelRoutes from "../ModelRoutes";

// `showModelRoutes` is false while the open step shows the two routes
// itself, so one screen has one box.
const TryOutFeatures = ({ onAddProviderKey, showModelRoutes = true }) => {
  return (
    <Box>
      <Typography
        variant="m2"
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
      >
        Explore
      </Typography>
      {showModelRoutes && (
        <ModelRoutes
          sentence="Experiments and evaluations call a model, and your organization has no provider key of its own."
          onAddProviderKey={onAddProviderKey}
        />
      )}
      <Box
        display={"flex"}
        flexDirection={"row"}
        gap={"12px"}
        sx={{ marginTop: "12px" }}
      >
        {featureCardList.map((item, index) => (
          <FeatureCard
            key={index}
            title={item.title}
            description={item.description}
            imageUrl={item.image}
            imageUrlDark={item.imageDark}
            buttonTitle={item.buttonTitle}
            learnMoreLink={item.learnMoreLink}
            actionLink={item.actionLink}
          />
        ))}
      </Box>
    </Box>
  );
};

TryOutFeatures.propTypes = {
  onAddProviderKey: PropTypes.func,
  showModelRoutes: PropTypes.bool,
};

export default TryOutFeatures;
