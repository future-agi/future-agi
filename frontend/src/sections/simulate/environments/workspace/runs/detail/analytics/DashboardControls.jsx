import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Menu,
  MenuItem,
  Stack,
  TextField,
} from "@mui/material";

export function useDashboardLayout(executionId) {
  const key = `simulation-analytics-layout-v1:${executionId}`;
  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      return {
        hidden: Array.isArray(value?.hidden)
          ? value.hidden.filter((v) => typeof v === "string")
          : [],
        views: Array.isArray(value?.views)
          ? value.views.filter(
              (v) => typeof v?.name === "string" && Array.isArray(v.hidden),
            )
          : [],
        active: typeof value?.active === "string" ? value.active : "Default",
      };
    } catch {
      return { hidden: [], views: [], active: "Default" };
    }
  };
  const [layout, setLayout] = useState(read);
  const [storageError, setStorageError] = useState(false);
  const update = (change) => {
    const next = change(layout);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      setStorageError(true);
    }
    setLayout(next);
  };
  return { ...layout, storageError, update };
}

export function printDashboard(element, title) {
  if (!element) return;
  const frame = document.createElement("iframe");
  frame.title = "Analytics PDF export";
  frame.style.cssText =
    "position:fixed;left:-10000px;top:0;width:1200px;height:900px;border:0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  document.querySelectorAll('style, link[rel="stylesheet"]').forEach((node) => {
    const copy = node.cloneNode(true);
    if (node.tagName === "STYLE") {
      try {
        copy.textContent = [...node.sheet.cssRules]
          .map((rule) => rule.cssText)
          .join("\n");
      } catch {
        /* Inline text remains available when CSS rules cannot be read. */
      }
    }
    doc.head.appendChild(copy);
  });
  const style = doc.createElement("style");
  style.textContent =
    "@page { size: A3 portrait; margin: 10mm; } body { margin: 0; padding: 16px; background: #0c0c0e; color: #eee; font-family: Arial,sans-serif; -webkit-print-color-adjust: exact; print-color-adjust: exact; } .analytics-no-print { display: none !important; } section { break-inside: avoid; }";
  doc.head.appendChild(style);
  const heading = doc.createElement("h1");
  heading.textContent = title;
  doc.title = title;
  doc.body.appendChild(heading);
  doc.body.appendChild(element.cloneNode(true));
  const cleanup = () => frame.remove();
  frame.contentWindow.addEventListener("afterprint", cleanup, { once: true });
  Promise.resolve(doc.fonts?.ready)
    .then(() => {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    })
    .catch(cleanup);
}

export default function DashboardControls({ layout, widgets, onPrint }) {
  const [menu, setMenu] = useState(null);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const save = () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === "Default") return;
    layout.update((current) => ({
      ...current,
      active: trimmed,
      views: [
        ...current.views.filter((v) => v.name !== trimmed),
        { name: trimmed, hidden: current.hidden },
      ],
    }));
    setSaving(false);
    setName("");
  };
  const show = (id) =>
    layout.update((current) => ({
      ...current,
      hidden: current.hidden.filter((v) => v !== id),
    }));
  return (
    <>
      <Stack
        direction="row"
        gap={1}
        flexWrap="wrap"
        className="analytics-no-print"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Button
          variant="outlined"
          size="small"
          onClick={(event) =>
            setMenu({ type: "views", anchor: event.currentTarget })
          }
        >
          {layout.active} ▾
        </Button>
        <Button variant="outlined" size="small" onClick={() => setSaving(true)}>
          + Save view
        </Button>
        <Box sx={{ flex: 1 }} />
        <Button
          variant="outlined"
          size="small"
          onClick={(event) =>
            setMenu({ type: "widgets", anchor: event.currentTarget })
          }
        >
          + Add widget
        </Button>
        <Button
          variant="outlined"
          size="small"
          onClick={(event) =>
            setMenu({ type: "hidden", anchor: event.currentTarget })
          }
        >
          Hidden ({layout.hidden.length})
        </Button>
        <Button variant="outlined" size="small" onClick={onPrint}>
          Export PDF
        </Button>
      </Stack>
      {layout.storageError && (
        <Alert severity="warning">
          This browser could not save the view. Your layout is available for
          this session.
        </Alert>
      )}
      <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)}>
        {menu?.type === "views"
          ? [
              <MenuItem
                key="default"
                onClick={() => {
                  layout.update((c) => ({
                    ...c,
                    active: "Default",
                    hidden: [],
                  }));
                  setMenu(null);
                }}
              >
                Default
              </MenuItem>,
              ...layout.views.map((view) => (
                <MenuItem
                  key={view.name}
                  onClick={() => {
                    layout.update((c) => ({
                      ...c,
                      active: view.name,
                      hidden: view.hidden,
                    }));
                    setMenu(null);
                  }}
                >
                  {view.name}
                </MenuItem>
              )),
            ]
          : widgets
              .filter(
                (w) => menu?.type !== "hidden" || layout.hidden.includes(w.id),
              )
              .map((widget) => (
                <MenuItem
                  key={widget.id}
                  onClick={() =>
                    layout.hidden.includes(widget.id)
                      ? show(widget.id)
                      : layout.update((c) => ({
                          ...c,
                          hidden: [...c.hidden, widget.id],
                        }))
                  }
                >
                  <Checkbox
                    checked={!layout.hidden.includes(widget.id)}
                    size="small"
                  />
                  {widget.title}
                </MenuItem>
              ))}
        {menu?.type === "hidden" && !layout.hidden.length && (
          <MenuItem disabled>No hidden widgets</MenuItem>
        )}
      </Menu>
      <Dialog
        open={saving}
        onClose={() => setSaving(false)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Save analytics view</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="View name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            helperText="Saved in this browser for this run."
            inputProps={{ maxLength: 80 }}
            onKeyDown={(event) => {
              if (event.key === "Enter") save();
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaving(false)}>Cancel</Button>
          <Button
            onClick={save}
            disabled={!name.trim() || name.trim() === "Default"}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
DashboardControls.propTypes = {
  layout: PropTypes.object.isRequired,
  widgets: PropTypes.array.isRequired,
  onPrint: PropTypes.func.isRequired,
};
