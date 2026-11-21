import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Button, IconButton, ListItemText, Menu, MenuItem, TableCell, Tooltip, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import Iconify from 'src/components/iconify';
import { ROW_ACTIONS_LABEL } from '../utils/terms';
import './skillIcons';

// One pattern for row actions in Skills tables (SKILL-TRACES §16 UXF-12; the
// design-system data-table rule): the whole row opens its primary target
// (Inspect, Open or Review; see `rowLinkProps` in responsiveTable.js), and
// that action and every other one sit in a trailing ⋯ menu. At most one
// action stays inline, as a small ghost button. On a phone (< sm) a row with
// a primary target is a tappable card with a trailing chevron and no button.

const stop = (event) => event.stopPropagation();

function MenuEntry({ item, onDone }) {
  const link = item.to && !item.disabled ? { component: RouterLink, to: item.to } : {};
  return (
    <MenuItem disabled={item.disabled} {...link} onClick={() => { if (item.disabled) return; onDone(); if (!item.to) item.onClick?.(); }}>
      <ListItemText primary={item.label} secondary={item.disabled ? item.reason : undefined}
        primaryTypographyProps={item.danger ? { color: 'error' } : undefined} />
    </MenuItem>
  );
}
MenuEntry.propTypes = { item: PropTypes.object.isRequired, onDone: PropTypes.func.isRequired };

/**
 * The trailing actions of one row.
 *
 * Props:
 *   name       row name for the menu's accessible name ("Actions for v2")
 *   primary    { label, onClick, disabled?, reason? }: what a row click opens;
 *              always the first ⋯ item. Use rowLinkProps(primary) on the row.
 *   actions    [{ label, onClick?, to?, disabled?, reason?, danger? }]: the other ⋯ items
 *   inline     'primary' to show the primary inline too (inbox "Open request"),
 *              or one other { label, onClick, disabled?, reason? } shown inline
 *              only (Linked skills "Check for update"). Always a small ghost
 *              button; never repeated in the ⋯ menu; not shown on a phone.
 *   phoneMenu  owner opt-in: keep the ⋯ (and no chevron) on a phone, for
 *              tables whose secondary actions are not reachable from the
 *              primary target. Off by default (UXF-12).
 */
export default function RowActions({ name, primary, actions = [], inline, phoneMenu = false, phone: phoneProp }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('sm'));
  const phone = phoneProp ?? narrow;
  const menuId = useId();
  const [anchor, setAnchor] = useState(null);
  const tappable = Boolean(primary && !primary.disabled && typeof primary.onClick === 'function');
  if (phone && tappable && !phoneMenu) {
    return (
      <Box aria-hidden data-row-chevron="true" sx={{ display: 'flex', color: 'text.secondary' }}>
        <Iconify icon="solar:alt-arrow-right-linear" width={16} />
      </Box>
    );
  }
  const items = [...(primary ? [primary] : []), ...actions];
  const inlineAction = inline === 'primary' ? primary : inline;
  const showInline = Boolean(inlineAction && !phone);
  const close = () => setAnchor(null);
  return (
    <Box onClick={stop} sx={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end', gap: 0.5, minWidth: 0 }}>
      {showInline && (
        <Tooltip title={inlineAction.disabled && inlineAction.reason ? inlineAction.reason : ''}>
          <span>
            <Button variant="text" size="small" color="inherit" disabled={inlineAction.disabled}
              onClick={(event) => inlineAction.onClick?.(event)}>
              {inlineAction.label}
            </Button>
          </span>
        </Tooltip>
      )}
      {items.length > 0 && (
        <>
          <IconButton size="small" color="inherit" aria-label={ROW_ACTIONS_LABEL(name)} aria-haspopup="menu"
            aria-controls={anchor ? menuId : undefined} aria-expanded={anchor ? 'true' : 'false'}
            onClick={(event) => setAnchor(event.currentTarget)}>
            <Iconify icon="solar:menu-dots-bold" width={16} sx={{ pointerEvents: 'none' }} />
          </IconButton>
          <Menu id={menuId} anchorEl={anchor} open={Boolean(anchor)} onClose={close} onClick={stop}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }} transformOrigin={{ vertical: 'top', horizontal: 'right' }}>
            {items.map((item) => <MenuEntry key={item.label} item={item} onDone={close} />)}
          </Menu>
        </>
      )}
    </Box>
  );
}

const actionShape = PropTypes.shape({
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  to: PropTypes.string,
  disabled: PropTypes.bool,
  reason: PropTypes.string,
  danger: PropTypes.bool,
});

RowActions.propTypes = {
  name: PropTypes.string,
  primary: actionShape,
  actions: PropTypes.arrayOf(actionShape),
  inline: PropTypes.oneOfType([PropTypes.oneOf(['primary']), actionShape]),
  phoneMenu: PropTypes.bool,
  phone: PropTypes.bool,
};

/**
 * The trailing table cell holding `RowActions`. On a phone, a tappable row's
 * cell is the chevron cell (`data-cell="row-chevron"`, placed by
 * stackedTableSx); otherwise it is the usual `data-cell="actions"` cell.
 */
export function RowActionsCell({ sx, ...props }) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const { primary, phoneMenu = false } = props;
  const chevron = phone && !phoneMenu && Boolean(primary && !primary.disabled && typeof primary.onClick === 'function');
  return (
    <TableCell data-cell={chevron ? 'row-chevron' : 'actions'} align="right" sx={{ width: { sm: '1%' }, whiteSpace: 'nowrap', ...sx }}>
      <RowActions {...props} phone={phone} />
    </TableCell>
  );
}
RowActionsCell.propTypes = { ...RowActions.propTypes, sx: PropTypes.object };
