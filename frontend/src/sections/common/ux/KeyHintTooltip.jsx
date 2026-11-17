import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { capForKey, keymapEntry, localiseCap } from "src/utils/keymap";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";

/** The caps for an action or a key list (`["mod+k"]` → `⌘K`). */
function capsFor({ action, keys }) {
  if (keys?.length) return keys.map(capForKey);
  const entry = action ? keymapEntry(action) : null;
  return entry ? [localiseCap(entry.display[0])] : [];
}

/** `Label  [K]` — the tooltip body with key caps. */
export function KeyHintTitle({ title, action, keys, description }) {
  const caps = capsFor({ action, keys });
  return (
    <Stack gap={0.5} sx={{ maxWidth: 300 }}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s2" color="inherit" sx={{ flex: 1 }}>
          {title}
        </Typography>
        {caps.length > 0 && (
          <Stack direction="row" gap={0.5} sx={{ flexShrink: 0 }}>
            {caps.map((c) => (
              <KeyCap
                key={c}
                sx={{ height: 20, minWidth: 20, typography: "s3" }}
              >
                {c}
              </KeyCap>
            ))}
          </Stack>
        )}
      </Stack>
      {description && (
        <Typography typography="s3" color="text.secondary">
          {description}
        </Typography>
      )}
    </Stack>
  );
}
KeyHintTitle.propTypes = {
  title: PropTypes.node,
  action: PropTypes.string,
  keys: PropTypes.arrayOf(PropTypes.string),
  description: PropTypes.node,
};

/**
 * KeyHintTooltip — a tooltip that names the action and shows its key:
 * `Next row  [J]`. Mac/Win aware (`⌘K` / `Ctrl+K`).
 *
 * Props (frozen):
 * @param {React.ReactNode} props.title  what the control does
 * @param {string} [props.action]  KEYMAP action (`next`, `density`, …)
 * @param {string[]} [props.keys]  or key strings (`["mod+k"]`, `["?"]`)
 * @param {React.ReactNode} [props.description]  a second, quieter line (why)
 * @param {string} [props.placement]
 * @param {React.ReactElement} props.children  the control (must hold a ref)
 */
export default function KeyHintTooltip({
  title,
  action,
  keys,
  description,
  placement = "bottom",
  children,
  ...rest
}) {
  return (
    <CustomTooltip
      show
      describeChild
      disableInteractive
      size="small"
      placement={placement}
      title={
        <KeyHintTitle
          title={title}
          action={action}
          keys={keys}
          description={description}
        />
      }
      {...rest}
    >
      {children}
    </CustomTooltip>
  );
}

KeyHintTooltip.propTypes = {
  title: PropTypes.node,
  action: PropTypes.string,
  keys: PropTypes.arrayOf(PropTypes.string),
  description: PropTypes.node,
  placement: PropTypes.string,
  children: PropTypes.element.isRequired,
};
