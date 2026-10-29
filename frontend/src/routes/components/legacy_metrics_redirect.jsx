import { Navigate, useLocation } from "react-router-dom";
import { paths } from "src/routes/paths";

export default function LegacyMetricsRedirect() {
  const { search, hash } = useLocation();
  return (
    <Navigate
      replace
      to={{ pathname: paths.dashboard.evalMetrics, search, hash }}
    />
  );
}
