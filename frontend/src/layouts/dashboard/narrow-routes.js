// Routes that drop the dashboard's 1200 px minimum width and, below md, open
// the sidebar as a drawer from a slim top bar: the AI Gateway, and the
// central billing pages its rail links to. Every other Future AGI page keeps
// its current layout.

import { paths } from "src/routes/paths";
import { isGatewayPath } from "src/sections/omega/shell/products";

const CENTRAL_BILLING_PATHS = [
  paths.dashboard.settings.billing,
  paths.dashboard.settings.usageSummary,
];

const underSegment = (pathname, root) =>
  pathname === root || pathname.startsWith(`${root}/`);

export function relaxesMinWidth(pathname = "") {
  return (
    isGatewayPath(pathname) ||
    CENTRAL_BILLING_PATHS.some((root) => underSegment(pathname, root))
  );
}
