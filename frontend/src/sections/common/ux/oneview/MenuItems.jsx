import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { capForKey, keymapEntry, localiseCap } from "src/utils/keymap";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";

/** Key caps for a menu row: a KEYMAP action or key strings. */
export function capsOf(shortcut) {
  if (!shortcut) return [];
  if (Array.isArray(shortcut)) return shortcut.map(capForKey);
  const entry = keymapEntry(shortcut);
  return entry ? [localiseCap(entry.display[0])] : [];
}

/**
 * Danger items last, after a divider; no leading / trailing / double
 * dividers. Pure.
 */
export function arrangeMenuItems(items) {
  const list = (items ?? []).filter(Boolean);
  const safe = list.filter((i) => !i.danger);
  const danger = list.filter((i) => i.danger && !i.divider);
  const all = danger.length ? [...safe, { divider: true }, ...danger] : safe;
  const out = [];
  all.forEach((i) => {
    if (i.divider && (!out.length || out[out.length - 1].divider)) return;
    out.push(i);
  });
  while (out.length && out[out.length - 1].divider) out.pop();
  return out;
}

/**
 * One menu row: icon · label (+ secondary) · key caps right. Disabled rows
 * keep their place and say why in a tooltip (they stay focusable so the
 * reason is reachable by keyboard too).
 */
export function MenuRow({ item, onRun, dense = true }) {
  const caps = capsOf(item.shortcut);
  const row = (
    <MenuItem
      dense={dense}
      aria-disabled={item.disabled || undefined}
      data-menu-item={item.id}
      onClick={(e) => {
        if (item.disabled) {
          e.preventDefault();
          return;
        }
        onRun?.(item, e);
      }}
      sx={{
        minHeight: 32,
        gap: 0,
        ...(item.disabled
          ? {
              opacity: 0.5,
              cursor: "default",
              "&:hover": { bgcolor: "transparent" },
            }
          : {}),
        ...(item.danger && !item.disabled ? { color: "error.main" } : {}),
      }}
    >
      <ListItemIcon sx={{ minWidth: 28, color: "inherit" }}>
        {item.checked ? (
          <Iconify icon="mdi:check" width={16} />
        ) : item.icon ? (
          <Iconify icon={item.icon} width={16} />
        ) : null}
      </ListItemIcon>
      <ListItemText
        primary={item.label}
        secondary={item.secondary}
        primaryTypographyProps={{ typography: "s2", noWrap: true }}
        secondaryTypographyProps={{ typography: "s3", noWrap: true }}
        sx={{ my: 0, minWidth: 0 }}
      />
      {caps.length > 0 && (
        <Stack direction="row" gap={0.5} sx={{ ml: 2, flexShrink: 0 }}>
          {caps.map((c) => (
            <KeyCap key={c} sx={{ height: 20, minWidth: 20, typography: "s3" }}>
              {c}
            </KeyCap>
          ))}
        </Stack>
      )}
    </MenuItem>
  );
  if (item.disabled && item.reason)
    return (
      <CustomTooltip
        show
        size="small"
        placement="right"
        title={item.reason}
        disableInteractive
      >
        {row}
      </CustomTooltip>
    );
  return row;
}

MenuRow.propTypes = {
  item: PropTypes.object.isRequired,
  onRun: PropTypes.func,
  dense: PropTypes.bool,
};

/**
 * MenuList of items `{id, label, icon?, shortcut?, checked?, secondary?,
 * run?, disabled?, reason?, danger?, divider?}` at an anchor element or a
 * point (`anchorPosition {top, left}`).
 */
export function ItemsMenu({
  open,
  onClose,
  items,
  anchorEl,
  anchorPosition,
  minWidth = 220,
  onRun,
  MenuListProps,
  ...rest
}) {
  const list = arrangeMenuItems(items);
  return (
    <Menu
      open={Boolean(open)}
      onClose={onClose}
      anchorEl={anchorPosition ? undefined : anchorEl}
      anchorReference={anchorPosition ? "anchorPosition" : "anchorEl"}
      anchorPosition={anchorPosition}
      PaperProps={{ sx: { minWidth, maxWidth: 360 } }}
      MenuListProps={{ dense: true, ...MenuListProps }}
      {...rest}
    >
      {list.map((item, i) =>
        item.divider ? (
          <Divider key={`d${i}`} sx={{ my: "4px !important" }} />
        ) : (
          <MenuRow
            key={item.id ?? i}
            item={item}
            onRun={(it, e) => {
              onClose?.();
              if (onRun) onRun(it, e);
              else it.run?.(e);
            }}
          />
        ),
      )}
      {list.length === 0 && (
        <Box sx={{ px: 2, py: 1, typography: "s2", color: "text.secondary" }}>
          Nothing to do here.
        </Box>
      )}
    </Menu>
  );
}

ItemsMenu.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  items: PropTypes.array,
  anchorEl: PropTypes.any,
  anchorPosition: PropTypes.object,
  minWidth: PropTypes.number,
  onRun: PropTypes.func,
  MenuListProps: PropTypes.object,
};
