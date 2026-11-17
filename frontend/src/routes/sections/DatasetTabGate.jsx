import { useEffect } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { unknownTabFallback } from "./datasetTabGate";

/** Wraps the dataset page: an unknown `?tab=` lands on Data with a notice. */
export default function DatasetTabGate({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const fallback = unknownTabFallback(location.search);
  useEffect(() => {
    if (!fallback) return;
    navigate(
      { pathname: location.pathname, search: fallback.search },
      { replace: true, state: location.state },
    );
    enqueueSnackbar(fallback.notice, {
      variant: "info",
      key: "dataset-unknown-tab",
      preventDuplicate: true,
    });
  }, [fallback, location.pathname, location.state, navigate]);
  return fallback ? null : children;
}
DatasetTabGate.propTypes = { children: PropTypes.node };
