import React, { useMemo, useRef, useCallback } from "react";
import { DevelopDetailContext } from "./Context/DevelopDetailContext";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";

const DevelopDetailProvider = ({ children }) => {
  const gridApi = useRef(null);
  const queryClient = useQueryClient();
  const refetchTable = useRef(null);

  const setGridApi = useCallback((api) => {
    gridApi.current = api;
  }, []);

  const setRefetchTable = (refetchFunc) => {
    refetchTable.current = refetchFunc;
  };

  const refreshGrid = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["dataset-detail"] });
    if (typeof refetchTable?.current === "function") {
      refetchTable.current();
    }
    gridApi.current?.refreshServerSide();
  }, [queryClient]);

  const contextValue = useMemo(
    () => ({
      gridApi,
      setGridApi,
      refreshGrid,
      setRefetchTable,
    }),
    [gridApi, setGridApi, refreshGrid],
  );

  return (
    <DevelopDetailContext.Provider value={contextValue}>
      {children}
    </DevelopDetailContext.Provider>
  );
};

DevelopDetailProvider.propTypes = {
  children: PropTypes.node,
};

export default DevelopDetailProvider;
