// [status, say] for a dialog's own status line (StatusLine.jsx): say(text,
// tone) sets the line and raises the toast, which shows beside the open
// dialog, or over it when there is no room beside it.
import { useCallback, useState } from "react";
import { enqueueSnackbar } from "src/components/snackbar";

export default function useStatusLine() {
  const [status, setStatus] = useState(null);
  const say = useCallback((text, tone = "success") => {
    setStatus({ text, tone });
    enqueueSnackbar(text, { variant: tone });
  }, []);
  return [status, say];
}
