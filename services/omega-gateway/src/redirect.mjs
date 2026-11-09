// Translate an EE Location into the gateway namespace without following it.
// This is response handling only: a client's next request still traverses every wall.
export function publicRedirect(location, target, config) {
  // WHATWG URL repairs backslashes, whitespace and incomplete absolute URLs. Do not
  // reinterpret those malformed references (or bad percent escapes) as trusted paths.
  if (
    typeof location !== "string" ||
    location.length === 0 ||
    /[\x00-\x20\x7f\\]/.test(location) ||
    /%(?![0-9a-f]{2})/i.test(location) ||
    /^(?:https?:)?\/\/\//i.test(location) ||
    (/^[a-z][a-z\d+.-]*:/i.test(location) && !/^https?:\/\//i.test(location))
  ) {
    return null;
  }
  let destination;
  const remote = new URL(config.remoteBaseUrl);
  try {
    destination = new URL(location, target);
  } catch {
    return null;
  }
  if (
    destination.origin !== remote.origin ||
    destination.username ||
    destination.password
  ) {
    return null;
  }

  // Invert upstreamUrl's mount mapping. A path outside the EE mount has no public
  // equivalent here; never silently map it to a different upstream resource.
  const mount = remote.pathname.replace(/\/+$/, "");
  if (
    mount &&
    destination.pathname !== mount &&
    !destination.pathname.startsWith(`${mount}/`)
  ) {
    return null;
  }
  const path = destination.pathname.slice(mount.length) || "/";
  // With an empty public mount, // would be an external network-path reference.
  if (path.startsWith("//")) return null;
  return `${config.basePath}${path}${destination.search}${destination.hash}`;
}
