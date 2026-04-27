import { useCallback, useRef, useState } from "react";
import PropTypes from "prop-types";
import React from "react";

import { ObserveHeaderContext } from "./ObserveHeaderContext";

const ObserveHeaderProvider = ({ children }) => {
  const [headerConfig, setHeaderConfig] = useState({
    text: "",
    filterTrace: null,
    filterSpan: null,
    selectedTab: null,
    filterSession: null,
    refreshData: null,
    resetFilters: null,
    gridApi: null,
  });

  const [activeViewConfig, setActiveViewConfig] = useState(null);

  // Ref (not state) so re-registering the callback doesn't re-render sibling consumers.
  const getViewConfigRef = useRef(null);
  const registerGetViewConfig = useCallback((fn) => {
    getViewConfigRef.current = typeof fn === "function" ? fn : null;
  }, []);
  const getViewConfig = useCallback(() => {
    const fn = getViewConfigRef.current;
    return typeof fn === "function" ? fn() : null;
  }, []);

  const getTabTypeRef = useRef(null);
  const registerGetTabType = useCallback((fn) => {
    getTabTypeRef.current = typeof fn === "function" ? fn : null;
  }, []);
  const getTabType = useCallback(() => {
    const fn = getTabTypeRef.current;
    return typeof fn === "function" ? fn() : "traces";
  }, []);

  return (
    <ObserveHeaderContext.Provider
      value={{
        headerConfig,
        setHeaderConfig,
        activeViewConfig,
        setActiveViewConfig,
        registerGetViewConfig,
        getViewConfig,
        registerGetTabType,
        getTabType,
      }}
    >
      {children}
    </ObserveHeaderContext.Provider>
  );
};

ObserveHeaderProvider.propTypes = {
  children: PropTypes.node,
};

export default ObserveHeaderProvider;
