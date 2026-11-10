import PropTypes from "prop-types";
import { Avatar } from "@mui/material";
import Iconify from "src/components/iconify";
import { PLATFORMS } from "./constants";

export default function PlatformLogo({ platform, size = 40 }) {
  const p = PLATFORMS.find((pl) => pl.id === platform);
  const logoSrc = p?.logo;

  if (logoSrc) {
    return (
      <Avatar
        src={logoSrc}
        variant="rounded"
        sx={{ width: size, height: size }}
      />
    );
  }

  if (p?.icon) {
    return (
      <Avatar
        variant="rounded"
        sx={{ width: size, height: size }}
      >
        <Iconify
          icon={p.icon}
          width={size * 0.6}
          sx={{ color: p.iconColor || "text.primary" }}
        />
      </Avatar>
    );
  }

  return (
    <Avatar variant="rounded" sx={{ width: size, height: size }}>
      <Iconify icon="solar:plug-circle-bold-duotone" width={size * 0.6} />
    </Avatar>
  );
}

PlatformLogo.propTypes = {
  platform: PropTypes.string,
  size: PropTypes.number,
};
