/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext, useMemo } from "react";
import PropTypes from "prop-types";

import { createDeploymentsApi } from "./deploymentsApi";

const defaultApi = createDeploymentsApi();
const DeploymentsApiContext = createContext(defaultApi);

/** Lets tests (and future callers) supply a different client. */
export function DeploymentsApiProvider({ api, children }) {
  const value = useMemo(() => api || defaultApi, [api]);
  return (
    <DeploymentsApiContext.Provider value={value}>
      {children}
    </DeploymentsApiContext.Provider>
  );
}

DeploymentsApiProvider.propTypes = {
  api: PropTypes.object,
  children: PropTypes.node,
};

export function useDeploymentsApi() {
  return useContext(DeploymentsApiContext);
}
