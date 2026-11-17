import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { PRODUCTION_IMPORT_READY } from "./productionImportEvents";

export default function useProductionImportRefresh(refreshGrid) {
  const { dataset } = useParams();
  useEffect(() => {
    const refresh = (event) => {
      if (dataset && event.detail?.datasetId === dataset) refreshGrid();
    };
    window.addEventListener(PRODUCTION_IMPORT_READY, refresh);
    return () => window.removeEventListener(PRODUCTION_IMPORT_READY, refresh);
  }, [dataset, refreshGrid]);
}
