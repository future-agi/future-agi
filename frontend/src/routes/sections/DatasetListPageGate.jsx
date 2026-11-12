import { useEffect } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { useDatasetListQuery } from "src/sections/develop/hooks/useDatasetListQuery";
import { listQueryArgs, pagePastEnd } from "./datasetListPage";

/**
 * Wraps the dataset list (L7c g1, url:owned-elsewhere): a `?page=` past the
 * last page opens the last page with a notice. It reads the list's own query
 * (same args, so the same cached answer) and never blocks the page.
 */
export default function DatasetListPageGate({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const args = listQueryArgs(location.search);
  const { data } = useDatasetListQuery(args, { enabled: args.page !== 0 });
  const fix =
    args.page !== 0 && data
      ? pagePastEnd(location.search, data.totalPages)
      : null;
  const fixSearch = fix ? fix.search : null;
  const notice = fix?.notice ?? null;
  useEffect(() => {
    if (fixSearch == null) return;
    navigate(
      {
        pathname: location.pathname,
        search: fixSearch ? `?${fixSearch}` : "",
        hash: location.hash,
      },
      { replace: true, state: location.state },
    );
    enqueueSnackbar(notice, {
      variant: "info",
      key: "dataset-list-page-past-end",
      preventDuplicate: true,
    });
  }, [
    fixSearch,
    notice,
    location.pathname,
    location.hash,
    location.state,
    navigate,
  ]);
  return children;
}
DatasetListPageGate.propTypes = { children: PropTypes.node };
