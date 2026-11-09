// Row layouts (W9-4): `Layout [Default ▾]` at the top of the Row tab. The
// menu lists Default, the layouts shared with everyone on the dataset, the
// user's own, then Customize / Copy / Delete.
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { notify } from "src/sections/common/ux/toast";
import { canDeleteLayout } from "./useRowLayouts";

const errText = (err) =>
  err?.response?.data?.result ??
  err?.response?.data?.message ??
  err?.message ??
  "The layout wasn't deleted.";

const Check = ({ on }) => (
  <ListItemIcon sx={{ minWidth: 28 }}>
    {on ? <Iconify icon="mdi:check" width={16} /> : null}
  </ListItemIcon>
);
Check.propTypes = { on: PropTypes.bool };

const Group = ({ title, views, selectedId, onPick }) =>
  views.length
    ? [
        <ListSubheader
          key={`h-${title}`}
          disableSticky
          sx={{ typography: "s3", lineHeight: "28px", color: "text.secondary" }}
        >
          {title}
        </ListSubheader>,
        ...views.map((v) => (
          <MenuItem
            key={v.id}
            selected={v.id === selectedId}
            onClick={() => onPick(v.id)}
            data-testid={`layout-option-${v.name}`}
          >
            <Check on={v.id === selectedId} />
            <ListItemText
              primary={v.name}
              secondary={v.mine === false && v.created_by ? v.created_by : null}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
          </MenuItem>
        )),
      ]
    : [];

/**
 * @param {{layouts: object, onCustomize: Function, onCopy: Function,
 *   children?: React.ReactNode}} props  `layouts` = useRowLayouts();
 *   Delete asks first and follows the server's rule (author or admin).
 */
const LayoutBar = ({ layouts, onCustomize, onCopy, children }) => {
  const anchor = useRef(null);
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const confirmDelete = async () => {
    setBusy(true);
    try {
      await layouts.remove(deleting.id);
      if (layouts.selected?.id === deleting.id) layouts.select(null);
      notify(`Deleted the layout “${deleting.name}”`, {
        key: "layout-deleted",
      });
      setDeleting(null);
    } catch (err) {
      notify(errText(err), { variant: "error", key: "layout-delete-failed" });
    } finally {
      setBusy(false);
    }
  };
  const { groups, selected, select } = layouts;
  const pick = (id) => {
    select(id);
    setOpen(false);
  };
  const act = (fn) => () => {
    setOpen(false);
    fn();
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ mb: 1, minHeight: 32 }}
      data-testid="row-layout-bar"
    >
      <Typography typography="s2" color="text.secondary">
        Layout
      </Typography>
      <Button
        ref={anchor}
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => setOpen(true)}
        endIcon={<Iconify icon="eva:chevron-down-fill" width={16} />}
        aria-haspopup="menu"
        aria-label={`Layout: ${selected?.name ?? "Default"}`}
        data-testid="row-layout-button"
        sx={{ typography: "s2", px: 1, minWidth: 0, borderColor: "divider" }}
      >
        {selected?.name ?? "Default"}
      </Button>
      {selected?.shared && (
        <Typography typography="s3" color="text.disabled" noWrap>
          {`For everyone on this dataset${
            selected.mine === false && selected.created_by
              ? ` · ${selected.created_by}`
              : ""
          }`}
        </Typography>
      )}
      <Box sx={{ flex: 1 }} />
      {children}
      <Menu
        anchorEl={anchor.current}
        open={open}
        onClose={() => setOpen(false)}
        slotProps={{ paper: { sx: { minWidth: 260 } } }}
      >
        <MenuItem
          selected={!selected}
          onClick={() => pick(null)}
          data-testid="layout-option-default"
        >
          <Check on={!selected} />
          <ListItemText
            primary="Default"
            primaryTypographyProps={{ typography: "s1" }}
          />
        </MenuItem>
        {Group({
          title: "For everyone on this dataset",
          views: groups.shared,
          selectedId: selected?.id,
          onPick: pick,
        })}
        {Group({
          title: "Yours",
          views: groups.yours,
          selectedId: selected?.id,
          onPick: pick,
        })}
        {(onCustomize || (selected && onCopy)) && <Divider />}
        {onCustomize && (
          <MenuItem onClick={act(onCustomize)} data-testid="layout-customize">
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify icon="mdi:view-dashboard-edit-outline" width={16} />
            </ListItemIcon>
            <ListItemText
              primary="Customize layout…"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
        )}
        {selected && onCopy && (
          <MenuItem onClick={act(onCopy)} data-testid="layout-copy">
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify icon="mdi:content-copy" width={16} />
            </ListItemIcon>
            <ListItemText
              primary="Copy to another dataset…"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
        )}
        {selected && canDeleteLayout(selected) && (
          <MenuItem
            onClick={act(() => setDeleting(selected))}
            data-testid="layout-delete"
            sx={{ color: "error.main" }}
          >
            <ListItemIcon sx={{ minWidth: 28, color: "inherit" }}>
              <Iconify icon="mdi:delete-outline" width={16} />
            </ListItemIcon>
            <ListItemText
              primary={`Delete “${selected.name}”`}
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
        )}
      </Menu>
      {deleting && (
        <ConfirmDialog
          open
          title={`Delete the layout “${deleting.name}”?`}
          body={
            deleting.shared
              ? "Everyone on this dataset loses it. Rows and values don't change."
              : "Only the layout goes. Rows and values don't change."
          }
          busy={busy}
          onConfirm={confirmDelete}
          onClose={() => setDeleting(null)}
        />
      )}
    </Stack>
  );
};

LayoutBar.propTypes = {
  layouts: PropTypes.shape({
    groups: PropTypes.object.isRequired,
    selected: PropTypes.object,
    select: PropTypes.func.isRequired,
    remove: PropTypes.func.isRequired,
  }).isRequired,
  onCustomize: PropTypes.func,
  onCopy: PropTypes.func,
  children: PropTypes.node,
};

export default LayoutBar;
