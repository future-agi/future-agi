import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import CodeIcon from "./CodeIcon";
import CodePanel from "./CodePanel";

/**
 * "Show as code" under a create form: the exact request the form's button
 * would send, rebuilt on every change. While the form would send nothing
 * (no request: a refused field, a missing choice), it says so instead of
 * showing a request the stack would refuse.
 *
 * Pass `request`, or `getRequest` when building it is not free (a deploy body
 * carries the whole bundle in base64): it runs only while the panel is open,
 * and again only when the function changes (memoize it on the form's inputs).
 * `idempotencyKey` is the form's own key for that request, when it has one.
 */
export default function ShowAsCode({
  request,
  getRequest,
  idempotencyKey,
  blockedText,
  testId,
}) {
  const [open, setOpen] = useState(false);
  const built = useMemo(
    () => (open && getRequest ? getRequest() : null),
    [open, getRequest],
  );
  const shown = getRequest ? built : request;
  return (
    <Box
      data-testid={testId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0 }}
    >
      <Button
        size="small"
        color="inherit"
        onClick={() => setOpen((value) => !value)}
        startIcon={<CodeIcon name="code" />}
        aria-expanded={open}
        data-testid="show-as-code"
        sx={{ alignSelf: "flex-start" }}
      >
        {open ? "Hide code" : "Show as code"}
      </Button>
      {open &&
        (shown ? (
          <CodePanel
            requests={[shown]}
            idempotencyKey={idempotencyKey}
            testId="show-as-code-panel"
          />
        ) : (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="show-as-code-blocked"
          >
            {blockedText}
          </Typography>
        ))}
    </Box>
  );
}

ShowAsCode.propTypes = {
  request: PropTypes.object,
  getRequest: PropTypes.func,
  idempotencyKey: PropTypes.string,
  blockedText: PropTypes.string.isRequired,
  testId: PropTypes.string,
};
