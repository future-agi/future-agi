import React from "react";
import PropTypes from "prop-types";

import { LogoMark } from "../../components/frame";
import { appForUrl } from "./knownApps";

// A server's mark: the app's logo when its URL is on a known app's domain
// (knownApps.js), else its initials on a neutral tile.
export default function ServerMark({ id, url, size = 32 }) {
  const app = appForUrl(url);
  return <LogoMark name={id} src={app?.logo} size={size} framed />;
}

ServerMark.propTypes = {
  id: PropTypes.string,
  url: PropTypes.string,
  size: PropTypes.number,
};
