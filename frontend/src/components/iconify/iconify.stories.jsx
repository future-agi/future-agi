import Iconify from "./iconify";

const meta = {
  component: Iconify,
  title: "UI Components/Iconify",
};

export default meta;

export const Default = {
  args: {
    icon: "solar:info-circle-linear",
    width: 40,
    sx: {},
  },
};

export const CustomColorIcon = {
  args: {
    icon: "solar:info-circle-linear",
    width: 40,
    sx: {
      color: "primary.main",
    },
  },
};
