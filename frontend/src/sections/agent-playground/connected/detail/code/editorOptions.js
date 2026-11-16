import { useCallback, useState } from "react";

export const editorTheme = (mode) => (mode === "dark" ? "vs-dark" : "light");

export const MIN_CODE_HEIGHT = 520;

export const EDITOR_OPTIONS = {
  readOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  fontSize: 12,
  lineNumbersMinChars: 3,
  renderLineHighlight: "none",
  wordWrap: "on",
  scrollbar: { alwaysConsumeMouseWheel: false, vertical: "hidden" },
};

export function useContentHeight() {
  const [height, setHeight] = useState(MIN_CODE_HEIGHT);
  const track = useCallback((...editors) => {
    const measure = () =>
      setHeight(
        Math.max(
          MIN_CODE_HEIGHT,
          ...editors.map((editor) => editor.getContentHeight()),
        ),
      );
    editors.forEach((editor) => editor.onDidContentSizeChange(measure));
    measure();
  }, []);
  return [height, track];
}
