import Tooltip from "@mui/material/Tooltip";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { styled, useTheme } from "@mui/material/styles";

// The design-system tooltip (theme MuiTooltip: inverse surface, 12px, radius 4,
// 240 wide). `size` and `type` are kept for existing callers but no longer
// change the look. `card` (or `expandable`) renders the DS hover card for rich
// content: breakdowns, previews, small tables.
const CTooltip = styled(
  ({ className, slotProps, size, expandable, card, classes, ...props }) => (
    <Tooltip
      {...props}
      slotProps={slotProps}
      classes={{
        ...classes,
        popper: className,
        ...((card || expandable) && {
          tooltip: [classes?.tooltip, "ds-hovercard"].filter(Boolean).join(" "),
        }),
      }}
    />
  ),
)({});

const EXPANDABLE_FLIP_PADDING = 300;

// R3 G0 (EL#20): one feel for every tooltip in the app.
// - 250 ms to open, instant when moving between neighbours (MUI's
//   `enterNextDelay` 0 hysteresis);
// - the popper flips to the side with room and keeps 8 px from the edges;
// - text tooltips are non-interactive: the popper takes no pointer events,
//   so a tooltip never blocks the next hover. Rich titles (elements) stay
//   hoverable (they may hold links); pass `disableInteractive` to opt out.
const ENTER_DELAY_MS = 250;
const DEFAULT_MODIFIERS = [
  { name: "flip", enabled: true, options: { padding: 8 } },
  { name: "preventOverflow", options: { padding: 8 } },
];

// IT-R1 G2 P8: a plain tooltip never stays pinned. While it shows, a click
// (outside the tooltip), a key press, a scroll or the trigger leaving the
// page closes it; it re-opens only after the pointer has left the trigger
// (a forced close is not undone by the next mouseover inside it).
const CLOSE_EVENTS = ["pointerdown", "keydown", "wheel"];
const ANCHOR_CHECK_MS = 300;
/** A pointer within this many px of where it was pressed has not moved. */
const STILL_PX = 3;
/** The anchor moving more than this closes its tooltip. */
const ANCHOR_MOVE_PX = 4;

// IT-R2 G2 P10 (reproduced: the voice wizard's ElevenLabs card and call-mode
// tooltips, the harness card tooltip): a click often swaps the content under
// a pointer that then stays still (a wizard step, a new card grid). The
// browser fires `mouseover` on whatever landed under it and the tooltip
// opened by itself over the neighbouring controls. A hover-open now needs
// hover INTENT: after a pointerdown, the pointer must move before a tooltip
// opens from a mouseover; the open waits for that move inside the trigger.
const pointerState = { downX: null, downY: null, movedSinceDown: true };
let pointerTracked = false;
function trackPointer() {
  if (pointerTracked || typeof window === "undefined") return;
  pointerTracked = true;
  window.addEventListener(
    "pointerdown",
    (e) => {
      pointerState.downX = e.clientX;
      pointerState.downY = e.clientY;
      pointerState.movedSinceDown = false;
    },
    true,
  );
  window.addEventListener(
    "pointermove",
    (e) => {
      if (pointerState.movedSinceDown || pointerState.downX == null) return;
      if (
        Math.abs(e.clientX - pointerState.downX) > STILL_PX ||
        Math.abs(e.clientY - pointerState.downY) > STILL_PX
      )
        pointerState.movedSinceDown = true;
    },
    true,
  );
}

/** True for an open that came from hovering, not from focus or a touch. */
const isHoverOpen = (e) => e?.type === "mouseover" || e?.type === "mouseenter";

const rectOf = (el) => {
  const r = el?.getBoundingClientRect?.();
  return r ? { x: r.left, y: r.top } : null;
};

/** Open state for an uncontrolled tooltip that closes on interaction. */
function useTransientOpen({ controlled, onOpen, onClose }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const anchorAtRef = useRef(null);
  const suppressRef = useRef(false);
  const rearmRef = useRef(null);
  const intentRef = useRef(null);
  const stopRearm = useCallback(() => {
    if (rearmRef.current)
      window.removeEventListener("pointermove", rearmRef.current, true);
    rearmRef.current = null;
  }, []);
  const stopIntent = useCallback(() => {
    if (intentRef.current)
      window.removeEventListener("pointermove", intentRef.current, true);
    intentRef.current = null;
  }, []);
  useEffect(() => {
    trackPointer();
    return () => {
      stopRearm();
      stopIntent();
    };
  }, [stopRearm, stopIntent]);
  const handleOpen = useCallback(
    (e) => {
      if (suppressRef.current) return;
      // MUI hands the mouseover event over after its enter delay, when
      // React has already cleared `currentTarget`; the target is inside the
      // trigger and moves with it.
      const anchor = e?.currentTarget ?? e?.target ?? anchorRef.current;
      anchorRef.current = anchor;
      if (isHoverOpen(e) && !pointerState.movedSinceDown) {
        // Content appeared under a still pointer: wait for a real move
        // inside the trigger before showing anything.
        stopIntent();
        intentRef.current = (ev) => {
          if (!pointerState.movedSinceDown) return;
          stopIntent();
          const a = anchorRef.current;
          if (!a || !a.isConnected || !a.contains(ev.target)) return;
          anchorAtRef.current = rectOf(a);
          setOpen(true);
          onOpen?.(ev);
        };
        window.addEventListener("pointermove", intentRef.current, true);
        return;
      }
      anchorAtRef.current = rectOf(anchor);
      setOpen(true);
      onOpen?.(e);
    },
    [onOpen, stopIntent],
  );
  const handleClose = useCallback(
    (e) => {
      stopIntent();
      setOpen(false);
      onClose?.(e);
    },
    [onClose, stopIntent],
  );
  useEffect(() => {
    if (controlled || !open) return undefined;
    const force = (e) => {
      const t = e?.target;
      if (t?.closest?.('[role="tooltip"]')) return;
      setOpen(false);
      // Stay closed until the pointer is somewhere else (MUI re-opens on
      // the next mouseover inside the trigger otherwise).
      suppressRef.current = true;
      stopRearm();
      rearmRef.current = (ev) => {
        const a = anchorRef.current;
        if (a && a.isConnected && a.contains(ev.target)) return;
        suppressRef.current = false;
        stopRearm();
      };
      window.addEventListener("pointermove", rearmRef.current, true);
    };
    CLOSE_EVENTS.forEach((n) => window.addEventListener(n, force, true));
    window.addEventListener("scroll", force, true);
    // The trigger left the page, or moved under a still pointer (a row
    // inserted above it, a panel opening): it never gets a mouseleave, so
    // close here.
    const timer = setInterval(() => {
      const a = anchorRef.current;
      if (a && !a.isConnected) {
        setOpen(false);
        return;
      }
      const was = anchorAtRef.current;
      const now = rectOf(a);
      if (
        was &&
        now &&
        (Math.abs(now.x - was.x) > ANCHOR_MOVE_PX ||
          Math.abs(now.y - was.y) > ANCHOR_MOVE_PX)
      )
        setOpen(false);
    }, ANCHOR_CHECK_MS);
    return () => {
      CLOSE_EVENTS.forEach((n) => window.removeEventListener(n, force, true));
      window.removeEventListener("scroll", force, true);
      clearInterval(timer);
    };
  }, [controlled, open, stopRearm]);
  return { open, handleOpen, handleClose };
}

const CustomTooltip = ({
  show,
  type,
  slotProps = {},
  size = "medium",
  expandable = false,
  card = false,
  ...props
}) => {
  // A theme's MuiTooltip defaults (tests, embeds) win over the app feel.
  const themeDefaults = useTheme()?.components?.MuiTooltip?.defaultProps ?? {};
  const enterDelay = themeDefaults.enterDelay ?? ENTER_DELAY_MS;
  const enterNextDelay = themeDefaults.enterNextDelay ?? 0;
  const controlled = props.open !== undefined;
  const transient = useTransientOpen({
    controlled,
    onOpen: props.onOpen,
    onClose: props.onClose,
  });
  if (!show) return <>{props.children}</>;

  let updatedSlotProps = { ...slotProps };
  const userModifiers = Array.isArray(updatedSlotProps.popper?.modifiers)
    ? updatedSlotProps.popper.modifiers
    : [];
  updatedSlotProps = {
    ...updatedSlotProps,
    popper: {
      ...(updatedSlotProps.popper || {}),
      modifiers: [
        ...DEFAULT_MODIFIERS.filter(
          (d) => !userModifiers.some((m) => m.name === d.name),
        ),
        ...userModifiers,
      ],
    },
  };

  if (expandable) {
    const existingPopper = updatedSlotProps.popper || {};
    const existingModifiers = Array.isArray(existingPopper.modifiers)
      ? existingPopper.modifiers
      : [];
    const hasFlip = existingModifiers.some((m) => m.name === "flip");
    const flipModifier = {
      name: "flip",
      options: { padding: { bottom: EXPANDABLE_FLIP_PADDING } },
    };

    updatedSlotProps = {
      ...updatedSlotProps,
      popper: {
        ...existingPopper,
        modifiers: hasFlip
          ? existingModifiers.map((m) =>
              m.name === "flip"
                ? {
                    ...m,
                    options: {
                      ...m.options,
                      padding: {
                        ...(typeof m.options?.padding === "object"
                          ? m.options.padding
                          : {}),
                        bottom: EXPANDABLE_FLIP_PADDING,
                      },
                    },
                  }
                : m,
            )
          : [...existingModifiers, flipModifier],
      },
    };
  }

  // Plain-text tooltips never take the pointer; a rich title (links,
  // buttons) stays hoverable unless the caller says otherwise.
  const richTitle = props.title != null && typeof props.title === "object";
  const interactive =
    expandable ||
    card ||
    props.disableInteractive === false ||
    (richTitle && props.disableInteractive !== true);
  return (
    <CTooltip
      size={size}
      expandable={expandable}
      card={card}
      enterDelay={enterDelay}
      enterNextDelay={enterNextDelay}
      disableInteractive={!interactive}
      slotProps={updatedSlotProps}
      {...props}
      {...(controlled
        ? {}
        : {
            open: transient.open,
            onOpen: transient.handleOpen,
            onClose: transient.handleClose,
          })}
    />
  );
};

CustomTooltip.propTypes = {
  ...Tooltip.propTypes,
};

export default CustomTooltip;
