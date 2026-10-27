import React from "react";
import SplashScreen from "./splash-screen";

export default {
  title: "Components/SplashScreen",
  component: SplashScreen,
  parameters: {
    layout: "centered",
  },
};

// Default splash screen
export const Default = {
  args: {},
};

// Custom background color
export const CustomBackground = {
  args: {
    sx: {
      bgcolor: "primary.main",
    },
  },
};

// Custom size
export const CustomSize = {
  args: {
    sx: {
      "& img": {
        height: "48px",
        width: "48px",
      },
      "& > div:nth-of-type(2)": {
        width: 150,
        height: 150,
      },
      "& > div:nth-of-type(3)": {
        width: 180,
        height: 180,
      },
    },
  },
};

// Custom animation speed
export const FastAnimation = {
  args: {
    sx: {
      "& > div:first-of-type": {
        "& > div": {
          transition: {
            duration: 1,
            repeatDelay: 0.5,
          },
        },
      },
      "& > div:nth-of-type(2), & > div:nth-of-type(3)": {
        "& > div": {
          transition: {
            duration: 1.6,
          },
        },
      },
    },
  },
};
