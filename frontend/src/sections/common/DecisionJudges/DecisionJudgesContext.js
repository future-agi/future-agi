import { createContext, useContext } from "react";

/**
 * Whether a screen's model menus list the decision catalog beyond the native
 * Jev models, where the launch switch shows it. A screen that lists it
 * provides true; a screen that lists only Jev provides false. A model
 * selector with no screen around it lists the catalog.
 */
export const DecisionJudgesContext = createContext(true);

export const useDecisionJudgesEnabled = () => useContext(DecisionJudgesContext);
