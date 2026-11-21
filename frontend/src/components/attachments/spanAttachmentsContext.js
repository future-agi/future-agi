import { createContext, useContext } from "react";
export const SpanAttachmentsContext = createContext(null);
export const useSpanClips = () => useContext(SpanAttachmentsContext)?.clips;
