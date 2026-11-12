/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext, useMemo } from "react";
import PropTypes from "prop-types";

import { createSandboxesApi } from "./sandboxesApi";

const defaultApi = createSandboxesApi();
const SandboxesApiContext = createContext(defaultApi);

/** Lets tests (and future callers) supply a different client. */
export function SandboxesApiProvider({ api, children }) {
  const value = useMemo(() => api || defaultApi, [api]);
  return (
    <SandboxesApiContext.Provider value={value}>
      {children}
    </SandboxesApiContext.Provider>
  );
}

SandboxesApiProvider.propTypes = {
  api: PropTypes.object,
  children: PropTypes.node,
};

export function useSandboxesApi() {
  return useContext(SandboxesApiContext);
}
