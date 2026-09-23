import { createContext } from "react";

/**
 * Shared drilldown handler. Any widget can call the context's value
 * with { title, subtitle, tasks } to open the run's task drawer
 * filtered to those tasks. Provided by RunAnalyticsV2 at the top
 * of the tree.
 */
export const DrilldownContext = createContext(null);
