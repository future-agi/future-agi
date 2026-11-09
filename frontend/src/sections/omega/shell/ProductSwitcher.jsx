import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Divider from "@mui/material/Divider";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";
import { useNavigate } from "react-router";

import Logo from "src/components/logo";
import SvgColor from "src/components/svg-color";
import { usePathname } from "src/routes/hooks";
import { GatewayIcon } from "src/sections/gateway/shell/gateway-icons";

import { InboxCount, NotifyMenuItem, TriggerDot } from "./ConsoleSwitcherParts";
import { inboxLabel, useConsoleInbox } from "./console-inbox";
import { NOTIFY_STATE, useNotificationState } from "./console-notify";
import { OmegaIcon } from "./icons";
import {
  CONSOLE_ACCESS,
  useConsoleAccess,
  VIEWER_SWITCHER_DETAIL,
} from "./omega-access";
import { PRODUCT, PRODUCTS, pathForProduct, productForPath } from "./products";
import { OMEGA_PRODUCT } from "./rollout";
import { useOmegaProducts } from "./useOmegaProducts";

// The Future AGI top-left mark, turned into a product switcher:
// Future AGI | AI Gateway | Omega Console. The Omega Console is listed only
// when VITE_OMEGA_CONSOLE=1 and the rollout admits it; the AI Gateway is
// always listed. The current product comes from the route.
// The "Omega Console" entry also carries the Console's Inbox count (CP P15)
// and, for a viewer, "Ask an admin for access" (CP P10).

const WORDMARK_SRC = "/logo/future_agi_text.svg";

function ProductMark({ productId, size = 18 }) {
  if (productId === PRODUCT.OMEGA_CONSOLE) {
    return (
      <OmegaIcon name="console" size={size} sx={{ color: "primary.main" }} />
    );
  }
  if (productId === PRODUCT.AI_GATEWAY) {
    return (
      <GatewayIcon name="gateway" size={size} sx={{ color: "primary.main" }} />
    );
  }
  return (
    <Logo
      disabledLink
      width={size}
      height={size}
      sx={{ width: size, height: size, alignItems: "center" }}
    />
  );
}

ProductMark.propTypes = {
  productId: PropTypes.string.isRequired,
  size: PropTypes.number,
};

function ProductName({ productId }) {
  if (productId === PRODUCT.FUTURE_AGI) {
    // The same wordmark the Logo draws. primary.main is #7857FC in light and
    // #FAFAFA in dark, which are exactly the Logo's wordmark fills.
    return (
      <SvgColor
        src={WORDMARK_SRC}
        role="img"
        aria-label="Future AGI"
        sx={{ width: 81, height: 13, color: "primary.main" }}
      />
    );
  }
  return (
    <Typography
      component="span"
      sx={{
        fontSize: 15,
        lineHeight: "18px",
        fontWeight: 600,
        letterSpacing: "-0.01em",
        color: "text.primary",
        whiteSpace: "nowrap",
      }}
    >
      {PRODUCTS.find((product) => product.id === productId)?.label}
    </Typography>
  );
}

ProductName.propTypes = { productId: PropTypes.string.isRequired };

function ProductTile({ productId }) {
  return (
    <Box
      sx={(theme) => ({
        width: 28,
        height: 28,
        borderRadius: "6px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        border: `1px solid ${theme.palette.divider}`,
        backgroundColor:
          productId === PRODUCT.FUTURE_AGI
            ? theme.palette.background.paper
            : alpha(theme.palette.primary.main, 0.08),
      })}
    >
      <ProductMark productId={productId} size={16} />
    </Box>
  );
}

ProductTile.propTypes = { productId: PropTypes.string.isRequired };

// With the Console not admitted for this workspace (READINESS 3.13) there is
// nothing to switch to: the plain Future AGI mark, no menu and no Console
// entry. Collapsed, nothing at all (the mini nav shows no mark of its own).
function StaticBrand({ collapsed, sx }) {
  if (collapsed) return null;
  return (
    <Box
      data-testid="omega-product-brand"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        ml: "8px",
        my: "4px",
        px: "4px",
        py: "4px",
        ...sx,
      }}
    >
      <ProductMark productId={PRODUCT.FUTURE_AGI} />
      <ProductName productId={PRODUCT.FUTURE_AGI} />
    </Box>
  );
}

StaticBrand.propTypes = { collapsed: PropTypes.bool, sx: PropTypes.object };

export default function ProductSwitcher({ collapsed = false, sx }) {
  const navigate = useNavigate();
  const pathname = usePathname();
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const omegaProducts = useOmegaProducts();
  const current = productForPath(pathname);
  const currentLabel = PRODUCTS.find((p) => p.id === current)?.label;
  const options = PRODUCTS.filter(
    (product) =>
      product.id !== PRODUCT.OMEGA_CONSOLE ||
      omegaProducts.listed(OMEGA_PRODUCT.CONSOLE),
  );
  // The Console's parts (its count, the notification opt-in) only with the
  // Console on offer for this workspace (READINESS 3.13).
  const consoleOffered = options.some((p) => p.id === PRODUCT.OMEGA_CONSOLE);
  const { access } = useConsoleAccess();
  const viewer = access === CONSOLE_ACCESS.VIEWER;
  const inbox = useConsoleInbox();
  const notify = useNotificationState();
  // Elsewhere in Future AGI the trigger says when the Console has approvals
  // waiting; on the Console page the Console's own sidebar shows them.
  const waiting =
    current !== PRODUCT.OMEGA_CONSOLE && !viewer && consoleOffered
      ? inboxLabel(inbox)
      : null;
  const triggerLabel = `Switch product (current: ${currentLabel})${
    waiting ? `, ${waiting} in Omega Console` : ""
  }`;
  const describe = (product) =>
    product.id === PRODUCT.OMEGA_CONSOLE && viewer
      ? VIEWER_SWITCHER_DETAIL
      : product.description;

  if (options.length < 2 && current === PRODUCT.FUTURE_AGI) {
    return <StaticBrand collapsed={collapsed} sx={sx} />;
  }

  const choose = (productId) => {
    setOpen(false);
    if (productId === current) return;
    navigate(pathForProduct(productId));
  };

  const trigger = collapsed ? (
    <Tooltip title={`${currentLabel} · Switch product`} placement="right">
      <ButtonBase
        ref={anchorRef}
        onClick={() => setOpen(true)}
        aria-label={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open ? "true" : "false"}
        aria-controls={open ? menuId : undefined}
        data-testid="omega-product-switcher"
        sx={{
          // Only the Console's waiting dot needs a positioned trigger; with no
          // Console on offer the trigger is exactly the AI Gateway line's.
          ...(waiting ? { position: "relative" } : {}),
          width: 36,
          height: 36,
          borderRadius: "6px",
          "&:hover": { backgroundColor: "action.hover" },
          ...sx,
        }}
      >
        <ProductMark productId={current} size={20} />
        <TriggerDot inbox={waiting ? inbox : null} />
      </ButtonBase>
    </Tooltip>
  ) : (
    <ButtonBase
      ref={anchorRef}
      onClick={() => setOpen(true)}
      aria-label={triggerLabel}
      aria-haspopup="menu"
      aria-expanded={open ? "true" : "false"}
      aria-controls={open ? menuId : undefined}
      data-testid="omega-product-switcher"
      sx={{
        ...(waiting ? { position: "relative" } : {}),
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        minWidth: 0,
        ml: "8px",
        my: "4px",
        px: "4px",
        py: "4px",
        borderRadius: "6px",
        justifyContent: "flex-start",
        "&:hover": { backgroundColor: "action.hover" },
        "&.Mui-focusVisible": {
          outline: (theme) => `2px solid ${theme.palette.primary.main}`,
          outlineOffset: 1,
        },
        ...sx,
      }}
    >
      <ProductMark productId={current} />
      <ProductName productId={current} />
      <OmegaIcon
        name="chevronsUpDown"
        size={14}
        sx={{ color: "text.disabled", ml: "2px" }}
      />
      <TriggerDot inbox={waiting ? inbox : null} />
    </ButtonBase>
  );

  return (
    <>
      {trigger}
      <Menu
        id={menuId}
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{
          vertical: collapsed ? "top" : "bottom",
          horizontal: collapsed ? "right" : "left",
        }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        MenuListProps={{ "aria-label": "Products", dense: true }}
        slotProps={{
          paper: {
            sx: {
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              // No boxShadow here: the Menu's paper is an overlay, so it takes
              // the theme's dropdown shadow (MuiPopover paper override).
              mt: collapsed ? 0 : 0.5,
              ml: collapsed ? 1 : 0,
              width: 260,
              py: 0.5,
            },
          },
        }}
      >
        <Typography
          component="li"
          aria-hidden
          sx={{
            display: "block",
            px: 2,
            pt: 0.75,
            pb: 0.5,
            fontSize: "11px",
            fontWeight: 600,
            // text.secondary, not text.disabled: 11 px text needs 4.5:1 in
            // both themes (axe color-contrast, CP A11Y1). Sentence case, as
            // the DS asks of every label.
            color: "text.secondary",
          }}
        >
          Products
        </Typography>
        {options.map((product) => {
          const selected = product.id === current;
          return (
            <MenuItem
              key={product.id}
              selected={selected}
              onClick={() => choose(product.id)}
              data-testid={`omega-product-option-${product.id}`}
              sx={{
                mx: 0.5,
                px: 1,
                py: 0.75,
                gap: 1.25,
                borderRadius: "6px",
                alignItems: "center",
                "&.Mui-selected, &.Mui-selected:hover": {
                  backgroundColor: "action.selected",
                },
              }}
            >
              <ProductTile productId={product.id} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  typography="s2_1"
                  fontWeight={500}
                  color="text.primary"
                  noWrap
                >
                  {product.label}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  noWrap
                  data-testid={`omega-product-detail-${product.id}`}
                >
                  {describe(product)}
                </Typography>
              </Box>
              {product.id === PRODUCT.OMEGA_CONSOLE && !viewer && (
                <InboxCount inbox={inbox} />
              )}
              {selected && (
                <OmegaIcon
                  name="check"
                  size={16}
                  sx={{ color: "primary.main" }}
                  aria-label="Current product"
                />
              )}
            </MenuItem>
          );
        })}
        {consoleOffered && !viewer && notify !== NOTIFY_STATE.UNSUPPORTED && (
          <Divider sx={{ my: 0.5 }} />
        )}
        {consoleOffered && !viewer && notify !== NOTIFY_STATE.UNSUPPORTED && (
          <NotifyMenuItem state={notify} onDone={() => setOpen(false)} />
        )}
      </Menu>
    </>
  );
}

ProductSwitcher.propTypes = {
  collapsed: PropTypes.bool,
  sx: PropTypes.object,
};
