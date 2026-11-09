import React, { useEffect, useState } from "react";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { useRunActionConfirmStore } from "./runActionConfirm";

/**
 * Dialog for confirmRunAction (Stop, Re-run): G0's named confirm (title
 * names the run, body says what is kept, `Keep running` focused).
 */
const RunActionConfirmHost = () => {
  const { request, hosts, mount, unmount, close } = useRunActionConfirmStore();
  const [token, setToken] = useState(null);
  useEffect(() => {
    const t = mount();
    setToken(t);
    return () => unmount(t);
  }, [mount, unmount]);
  if (token == null || hosts[0] !== token) return null;
  const answer = (ok) => {
    request?.resolve?.(ok);
    close();
  };
  return (
    <ConfirmDialog
      open={Boolean(request)}
      onClose={() => answer(false)}
      onConfirm={() => answer(true)}
      title={request?.title ?? ""}
      body={request?.content}
      confirmLabel={request?.confirmLabel ?? "Confirm"}
      cancelLabel={request?.cancelLabel ?? "Cancel"}
      danger={Boolean(request?.danger)}
    />
  );
};

export default RunActionConfirmHost;
