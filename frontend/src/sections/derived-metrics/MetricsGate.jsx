import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import { LoadingScreen } from "src/components/loading-screen";
import { useMetricsEnabled } from "./useMetricsEnabled";

export default function MetricsGate({ children }) {
  const { enabled, isLoading, isError, refetch } = useMetricsEnabled();

  if (isLoading) return <LoadingScreen />;
  if (isError) {
    return (
      <Alert
        severity="error"
        action={
          <Button color="inherit" onClick={() => refetch()}>
            Retry
          </Button>
        }
      >
        Couldn&apos;t check Metrics availability. Try again.
      </Alert>
    );
  }
  return enabled ? children : <Navigate to="/404" replace />;
}

MetricsGate.propTypes = {
  children: PropTypes.node,
};
