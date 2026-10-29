import { Navigate, useLocation } from "react-router-dom";
import { paths } from "src/routes/paths";

export default function LegacyMetricsRedirect() {
  const { pathname, search, hash } = useLocation();
  const metricsPath = pathname.replace(
    /^\/dashboard\/derived-metrics(?=\/|$)/i,
    paths.dashboard.metrics.root,
  );
  return <Navigate replace to={{ pathname: metricsPath, search, hash }} />;
}
