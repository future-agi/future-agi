import { matchRoutes } from "react-router-dom";

// Use the same branch ranking as useRoutes. Render only route definitions:
// match.pathname, match.pathnameBase and param *values* contain local IDs.
export function analyticsRoutePath(routes, pathname) {
  const matches = matchRoutes(routes ?? [], { pathname });
  if (!matches) return "/:id";

  let segments = [];
  for (const { route, params } of matches) {
    if (!route.path) continue; // Index routes and pathless layouts add no path.
    if (route.path.startsWith("/")) segments = []; // Absolute child template.
    for (const part of route.path.split("/")) {
      if (!part) continue;
      if (part.startsWith(":") && part.endsWith("?")) {
        if (params[part.slice(1, -1)] == null) continue;
      }
      // Parameters, splats (including a 404 catch-all), and unfamiliar route
      // syntax get a single placeholder independent of the input's contents.
      segments.push(/^[\w-]+$/.test(part) ? part : ":id");
    }
  }
  return `/${segments.join("/")}`;
}
