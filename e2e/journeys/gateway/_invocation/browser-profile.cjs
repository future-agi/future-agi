"use strict";
// Browser fixture documents have dynamic loopback URLs, not a stock app profile.
// Both kinds still require an explicit runner-admitted viewport and observer.
function browserProfile(value) {
  if (
    !value ||
    !Number.isSafeInteger(value.width) ||
    value.width < 320 ||
    !Number.isSafeInteger(value.height) ||
    value.height < 1 ||
    typeof value.widthAuthority !== "string" ||
    !value.widthAuthority.trim()
  ) {
    throw new Error(
      "browser context requires explicit admitted width, height and width authority",
    );
  }
  return {
    viewport: { width: value.width, height: value.height },
    widthAuthority: value.widthAuthority,
  };
}
function loopback(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("binding requires an explicit loopback origin");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("binding requires an explicit loopback origin");
  }
  return url.origin;
}
function fragmentProfile(value) {
  return {
    ...browserProfile(value),
    appUrl: loopback(value.appUrl),
    gatewayUrl: loopback(value.gatewayUrl),
  };
}
module.exports = { browserProfile, fragmentProfile };
