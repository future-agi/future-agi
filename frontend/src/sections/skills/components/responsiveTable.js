import { createElement, useCallback, useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Box } from '@mui/material';

// Two ways for a Skills table to survive a 390px phone (UX-R7 §1.6, iteration 1 SH6).

// Pixel strings on purpose: in sx a bare `1` means 100%, and a 100%-wide
// absolutely positioned header widened the whole document (iteration 2 SH1).
const visuallyHidden = {
  position: 'absolute', width: '1px', height: '1px', p: 0, m: '-1px', overflow: 'hidden',
  clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
};
const CHECKBOX = '.MuiTableCell-paddingCheckbox';
const ACTIONS = '[data-cell="actions"]';
const ROW_LINK = '[data-row-link="true"]';
const ROW_CHEVRON = '[data-cell="row-chevron"]';
const TITLE = `& tbody td:first-of-type:not(${CHECKBOX}), & tbody td${CHECKBOX} + td`;
const FIRST_DETAIL = `& tbody td:first-of-type:not(${CHECKBOX}) + td, & tbody td${CHECKBOX} + td + td`;

/**
 * `sx` for a Table that becomes a stacked list below `sm`:
 * each row is a block with a hairline divider; the first cell (after an
 * optional checkbox) is the s1 title on one line with an ellipsis; the other
 * cells are s3 secondary text joined with " · " (a rich first cell keeps all
 * its lines; only plain text truncates); cells marked
 * `data-cell="actions"` get their own line. The header stays in the
 * accessibility tree but is visually hidden. Desktop layout is untouched.
 * Rows built with `rowLinkProps` become tappable cards whose
 * `data-cell="row-chevron"` cell (from `RowActionsCell`) sits on the right.
 */
export const stackedTableSx = (theme) => ({
  [theme.breakpoints.down('sm')]: {
    display: 'block',
    '& thead': visuallyHidden,
    '& tbody': { display: 'block' },
    '& tbody tr': {
      display: 'block', height: 'auto', py: 1.5,
      borderBottom: `1px solid ${theme.palette.divider}`,
    },
    '& tbody td': {
      display: 'inline-block', maxWidth: 1, border: 0, p: 0, verticalAlign: 'baseline',
      ...theme.typography.s3, color: 'text.secondary',
    },
    '& tbody td:not(:first-of-type)::before': { content: '" · "', whiteSpace: 'pre' },
    [`& tbody td${CHECKBOX}`]: { float: 'left', mr: 1, mt: -0.5, width: 'auto' },
    [`${FIRST_DETAIL}`]: { '&::before': { content: 'none' } },
    // Plain-text titles stay on one line with an ellipsis. Rich title cells
    // (a Stack with notes or controls) keep every line: their children wrap.
    [TITLE]: {
      display: 'block', mb: 0.5, ...theme.typography.s1, color: 'text.primary',
      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      '&::before': { content: 'none' },
      '& > *': { whiteSpace: 'normal' },
    },
    [`& tbody td${ACTIONS}`]: { display: 'block', mt: 1, '&::before': { content: 'none' } },
    // Tappable cards (UXF-12): a row that opens its primary target keeps room
    // on the right for the trailing chevron, which sits centred on the card.
    [`& tbody tr${ROW_LINK}`]: { position: 'relative', pr: 4 },
    [`& tbody td${ROW_CHEVRON}`]: {
      position: 'absolute', right: 0, top: '50%', transform: 'translateY(-50%)',
      display: 'flex', maxWidth: 'none', color: 'text.secondary', '&::before': { content: 'none' },
    },
  },
});

/**
 * Keeps a real table and lets it scroll sideways inside its own region, with
 * a fade on the right edge while more columns are hidden. Cells keep their
 * natural width, so names never break one character per line.
 */
export function ScrollTable({ children, label, sx }) {
  const ref = useRef(null);
  const [more, setMore] = useState(false);
  const measure = useCallback(() => {
    const node = ref.current;
    if (node) setMore(node.scrollLeft + node.clientWidth < node.scrollWidth - 1);
  }, []);
  useEffect(() => {
    measure();
    if (typeof ResizeObserver === 'undefined' || !ref.current) return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [measure]);
  return createElement(Box, { sx: { position: 'relative', minWidth: 0, maxWidth: 1, ...sx } },
    createElement(Box, {
      ref, onScroll: measure, role: 'region', 'aria-label': label, tabIndex: 0,
      sx: {
        overflowX: 'auto', minWidth: 0,
        '&:focus-visible': { outline: 2, outlineColor: 'primary.main', outlineOffset: 2 },
        '& td, & th': { minWidth: 'min-content', overflowWrap: 'normal', wordBreak: 'normal' },
      },
    }, children),
    createElement(Box, {
      'aria-hidden': true, 'data-scroll-fade': more ? 'on' : 'off',
      sx: (theme) => ({
        position: 'absolute', top: 0, right: 0, bottom: 0, width: 24, pointerEvents: 'none',
        opacity: more ? 1 : 0, transition: 'opacity 120ms',
        background: `linear-gradient(to right, transparent, ${theme.palette.background.paper})`,
      }),
    }));
}
ScrollTable.propTypes = { children: PropTypes.node, label: PropTypes.string.isRequired, sx: PropTypes.object };

/**
 * Opt-in `sx` for a Skills table whose last column ends at the right edge of
 * the page: on desktop the last column keeps 72px clear on its right, so the
 * floating assistant (40px, 16px from the edge) never sits over a row action.
 */
export const fabGutterSx = (theme) => ({
  [theme.breakpoints.up('md')]: {
    '& tr > th:last-of-type, & tr > td:last-of-type': { pr: 9 },
  },
});

// Controls inside a row keep their own click; only the rest of the row opens it.
const ROW_IGNORE = 'a,button,input,select,textarea,label,[role="button"],[role="checkbox"],[role="menuitem"],[role="menu"],[role="switch"],[data-row-ignore]';

/**
 * Props for a `TableRow` whose whole row opens its primary target (UXF-12,
 * design-system data-table rule: clicking the row opens it, every action is in
 * the trailing ⋯). Pass the same `primary` you give `RowActionsCell`.
 *
 *   <TableRow {...rowLinkProps(primary)}> … <RowActionsCell primary={primary} … /></TableRow>
 *
 * primary: { label, onClick, disabled? }. A disabled or missing primary gives
 * an ordinary row. Clicks on links, buttons, checkboxes and menus inside the
 * row, and clicks that end a text selection, do not open the row. Enter on the
 * focused row opens it; the same action is also the first item in the ⋯ menu.
 */
export function rowLinkProps(primary) {
  if (!primary || primary.disabled || typeof primary.onClick !== 'function') return {};
  return {
    hover: true,
    tabIndex: 0,
    'data-row-link': 'true',
    onClick: (event) => {
      if (event.defaultPrevented) return;
      const control = event.target instanceof Element ? event.target.closest(ROW_IGNORE) : null;
      if (control && control !== event.currentTarget && event.currentTarget.contains(control)) return;
      const selection = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
      if (selection && !selection.isCollapsed && String(selection).trim()) return;
      primary.onClick(event);
    },
    onKeyDown: (event) => {
      if (event.key !== 'Enter' || event.target !== event.currentTarget) return;
      event.preventDefault();
      primary.onClick(event);
    },
    sx: {
      cursor: 'pointer',
      '&:focus-visible': { outline: 2, outlineColor: 'primary.main', outlineOffset: -2 },
    },
  };
}
