import { createContext, useContext } from "react";

// How the Console page tells the shell its frame is on screen. While the
// frame is not shown (loading, unavailable, refused) the shell keeps a slim
// bar with the product switcher, so the user can always get back to Future
// AGI. Once the frame reports ready, the EE Console's own sidebar and its
// "Future AGI" entry take over and the shell draws no chrome at all.
//
//   const { setFrameShown } = useOmegaConsoleChrome();
//   useEffect(() => { setFrameShown(gate === "ready"); }, [gate]);

export const OmegaConsoleChromeContext = createContext({
  frameShown: false,
  setFrameShown: (_shown) => {},
});

export function useOmegaConsoleChrome() {
  return useContext(OmegaConsoleChromeContext);
}
