import { createContext, useContext } from "react";

/**
 * True inside an evaluator editor that saves a decision question with the
 * eval (the evaluator picker): its model menus list the decision models.
 * Editors that cannot save one leave them out.
 */
export const DecisionJudgesContext = createContext(false);

export const useDecisionJudgesEnabled = () => useContext(DecisionJudgesContext);
