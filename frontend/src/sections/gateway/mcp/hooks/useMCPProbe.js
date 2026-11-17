import { useMutation } from "@tanstack/react-query";
import axios from "src/utils/axios";

import { MCP_API } from "../api";

// "Test connection": POST /agentcc/mcp/probe/ asks the gateway to initialize
// against an upstream URL and list its tools. Nothing is stored: a typed
// secret is used for this probe only; a credential_id is resolved from the
// org's vault. Org owners and admins only, 10 a minute per org.
export function useMCPProbe() {
  return useMutation({
    mutationFn: async (body) => {
      const { data } = await axios.post(MCP_API.probe, body);
      return data.result;
    },
  });
}
