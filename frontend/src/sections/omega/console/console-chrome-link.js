// Tells the shell whether the Console frame is on screen.
//
// Lane shell owns the hook (src/sections/omega/shell/console-chrome.js): while
// the frame is not shown, the shell keeps a slim bar with the product
// switcher so there is always a way back to Future AGI; once the frame holds
// its key, the Console's own sidebar and "Future AGI" entry take over and the
// shell hides that bar. This lane may not edit or require the shell's files,
// which land in a separate merge, so the hook is linked only when the file is
// part of the build. Until then the link is a no-op.
const shellModules =
  /** @type {Record<string, typeof import("../shell/console-chrome")>} */ (
    import.meta.glob("/src/sections/omega/shell/console-chrome.js", {
      eager: true,
    })
  );

const NO_SHELL_CHROME = Object.freeze({
  frameShown: false,
  setFrameShown: (_shown) => {},
});

const useNoShellChrome = () => NO_SHELL_CHROME;

const shellHook = Object.values(shellModules)[0]?.useOmegaConsoleChrome;

export const hasShellChrome = typeof shellHook === "function";

// Picked once when the module loads, so every render calls the same hook.
export const useConsoleChrome = hasShellChrome ? shellHook : useNoShellChrome;
