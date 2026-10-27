import { createContext, useContext } from "react";

// Set only on the public /shared/<token> page. Holds the share token and the
// resolved issue payload ({ issue, overview, traces, trends, sidebar }), so
// the issue page renders read-only from it and never fetches with sign-in.
export const SharedIssueContext = createContext(null);

export const useSharedIssue = () => useContext(SharedIssueContext);
