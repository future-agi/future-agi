import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, ListItemIcon, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyText, useCopiedFlash } from "./CopyButton";
import { notify } from "./toast";

/** `ID 00000012`: the last `short` characters of an id (dashes dropped). */
export const shortId = (id, short = 8) => {
  const s = String(id ?? "").replace(/-/g, "");
  return s.length > short ? s.slice(-short) : s;
};

/**
 * CopyId — an id chip: `ID 00000012` in mono `s3`. Click copies the FULL
 * id (in-place check); Shift+click copies `apiPath` when given; right-click
 * opens `Copy id · Copy API path · Copy as API call · Copy link` (each API
 * item only when its prop is given).
 *
 * Props (frozen):
 * @param {string} props.id
 * @param {number} [props.short=8]
 * @param {string} [props.apiPath]  e.g. `GET /model-hub/rows/<id>`
 * @param {string} [props.apiCall]  a command that sends that request
 * @param {string} [props.link]  a deep link for `Copy link`
 * @param {string} [props.label="ID"]  the prefix word
 * @param {object} [props.sx]
 */
export default function CopyId({
  id,
  short = 8,
  apiPath,
  apiCall,
  link,
  label = "ID",
  sx,
}) {
  const [copied, flash] = useCopiedFlash();
  const [menu, setMenu] = useState(null);
  if (!id) return null;
  const doCopy = async (text, what) => {
    const ok = await copyText(text);
    if (ok) flash();
    else
      notify("Couldn't copy. Your browser blocked the clipboard.", {
        variant: "error",
        key: "copy-failed",
      });
    return ok && what;
  };
  const tip = copied
    ? "Copied"
    : `Copy ${label === "ID" ? "id" : label.toLowerCase()} ${id}${apiPath ? " · Shift+click: API path" : ""}`;
  return (
    <>
      <CustomTooltip show size="small" placement="top" title={tip}>
        <Box
          component="button"
          type="button"
          aria-label={`Copy ${label} ${id}`}
          data-copy-id={id}
          onClick={(e) => {
            e.stopPropagation();
            if (e.shiftKey && apiPath) doCopy(apiPath, "api");
            else doCopy(id, "id");
          }}
          onContextMenu={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMenu({ x: e.clientX, y: e.clientY });
          }}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 0.75,
            height: 22,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.75,
            bgcolor: "background.neutral",
            color: "text.secondary",
            cursor: "pointer",
            font: "inherit",
            whiteSpace: "nowrap",
            "&:hover": { color: "text.primary", borderColor: "text.disabled" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
            ...sx,
          }}
        >
          <Typography
            component="span"
            typography="s3"
            sx={{ fontFamily: "monospace", fontVariantNumeric: "tabular-nums" }}
          >
            {label} {shortId(id, short)}
          </Typography>
          <Iconify
            icon={copied ? "mdi:check" : "mdi:content-copy"}
            width={12}
            sx={{ color: copied ? "green.600" : "inherit" }}
          />
        </Box>
      </CustomTooltip>
      <Menu
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={menu ? { top: menu.y, left: menu.x } : undefined}
      >
        <MenuItem
          onClick={() => {
            setMenu(null);
            doCopy(id, "id");
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:identifier" width={18} />
          </ListItemIcon>
          Copy id
        </MenuItem>
        {apiPath && (
          <MenuItem
            onClick={() => {
              setMenu(null);
              doCopy(apiPath, "api");
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:api" width={18} />
            </ListItemIcon>
            Copy API path
          </MenuItem>
        )}
        {apiCall && (
          <MenuItem
            onClick={() => {
              setMenu(null);
              doCopy(apiCall, "call");
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:console" width={18} />
            </ListItemIcon>
            Copy as API call
          </MenuItem>
        )}
        <MenuItem
          onClick={() => {
            setMenu(null);
            doCopy(link ?? window.location.href, "link");
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:link-variant" width={18} />
          </ListItemIcon>
          Copy link
        </MenuItem>
      </Menu>
    </>
  );
}

CopyId.propTypes = {
  id: PropTypes.string,
  short: PropTypes.number,
  apiPath: PropTypes.string,
  apiCall: PropTypes.string,
  link: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
};
