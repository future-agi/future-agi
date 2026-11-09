import { useEffect, useMemo, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Box, Button, ButtonBase, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import ModalWrapper from 'src/components/ModalWrapper/ModalWrapper';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { buildFileTree, hasControlCharacters } from '../utils/paths';
import { LIMITS } from '../constants';
import FileStatusBadge from './FileStatusBadge';
import './skillIcons';

const DEFAULT_PINNED = ['SKILL.md'];

export default function FileTree({ files, selectedPath, onSelect, onCreate, onRename, onDelete, pinned = DEFAULT_PINNED, protectedPaths = DEFAULT_PINNED, isPathReadOnly = () => false, readOnly = true }) {
  const [collapsed, setCollapsed] = useState(new Set());
  const [focused, setFocused] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [pathInput, setPathInput] = useState('');
  const [folderPrefix, setFolderPrefix] = useState('');
  const refs = useRef(new Map());
  useEffect(() => {
    // Preview links may select a file inside a collapsed ancestor.
    const parts = (selectedPath || '').split('/');
    setCollapsed((previous) => {
      const ancestors = parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
      if (!ancestors.some((path) => previous.has(path))) return previous;
      return new Set([...previous].filter((path) => !ancestors.includes(path)));
    });
  }, [selectedPath]);
  const rows = useMemo(() => {
    const tree = buildFileTree(files);
    tree.sort((a, b) => {
      const ai = pinned.indexOf(a.path);
      const bi = pinned.indexOf(b.path);
      return (ai < 0 ? Infinity : ai) - (bi < 0 ? Infinity : bi) || 0;
    });
    const visible = [];
    const visit = (nodes, level, parent) => nodes.forEach((node, index) => {
      visible.push({ ...node, level, parent, position: index + 1, setSize: nodes.length });
      if (node.children && !collapsed.has(node.path)) visit(node.children, level + 1, node.path);
    });
    visit(tree, 1, null);
    return visible;
  }, [files, pinned, collapsed]);
  const activePath = rows.some((row) => row.path === focused) ? focused : rows.find((row) => row.path === selectedPath)?.path || rows[0]?.path;
  const selectedFile = files.find((file) => file.path === selectedPath);
  const protectedRoot = protectedPaths.includes(selectedPath);
  const selectedReadOnly = Boolean(selectedFile?.readOnly || isPathReadOnly(selectedPath || ''));
  const cannotChangeSelected = readOnly || protectedRoot || selectedReadOnly;
  const protectedReason = protectedRoot ? `${selectedPath} is required and cannot be renamed or deleted.` : 'Linked and generated files are read-only.';
  const focus = (path) => { setFocused(path); refs.current.get(path)?.focus(); };
  const toggle = (path, collapse) => setCollapsed((previous) => {
    const next = new Set(previous);
    if (collapse) next.add(path); else next.delete(path);
    return next;
  });
  const activate = (row) => {
    if (row.type === 'folder') toggle(row.path, !collapsed.has(row.path));
    else onSelect(row.path);
  };
  const onKeyDown = (event, row, index) => {
    const folder = row.type === 'folder';
    // ButtonBase owns Enter/Space activation; only handle tree navigation here.
    if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    switch (event.key) {
      case 'ArrowDown': focus(rows[Math.min(index + 1, rows.length - 1)].path); break;
      case 'ArrowUp': focus(rows[Math.max(index - 1, 0)].path); break;
      case 'Home': focus(rows[0].path); break;
      case 'End': focus(rows[rows.length - 1].path); break;
      case 'ArrowRight':
        if (folder && collapsed.has(row.path)) toggle(row.path, false);
        else if (folder && rows[index + 1]?.parent === row.path) focus(rows[index + 1].path);
        break;
      case 'ArrowLeft':
        if (folder && !collapsed.has(row.path)) toggle(row.path, true);
        else if (row.parent) focus(row.parent);
        break;
      default: activate(row);
    }
  };
  const openDialog = (kind) => {
    setPathInput(kind === 'rename' ? selectedPath : folderPrefix);
    setDialog(kind);
  };
  const pathError = (() => {
    if (!pathInput) return 'Enter a relative path.';
    const segments = pathInput.split('/');
    if (pathInput.length > LIMITS.MAX_PATH || segments.length > LIMITS.MAX_PATH_SEGMENTS || pathInput.includes('\\') || hasControlCharacters(pathInput) || segments.some((part) => !part || part === '.' || part === '..' || /[ .]$/.test(part))) return 'Use a relative path without empty segments, backslashes, or dot segments.';
    if (isPathReadOnly(pathInput) || protectedPaths.some((path) => path.toLowerCase() === pathInput.toLowerCase())) return 'That path is reserved and cannot be created or replaced.';
    const lower = pathInput.toLowerCase();
    if (files.some((file) => {
      if (dialog === 'rename' && file.path === selectedPath) return false;
      const existing = file.path.toLowerCase();
      return existing === lower || lower.startsWith(`${existing}/`) || (dialog !== 'folder' && existing.startsWith(`${lower}/`));
    })) return 'That path is already used by a file or folder.';
    return '';
  })();
  const submitPath = () => {
    if (pathError || readOnly || (dialog === 'rename' && cannotChangeSelected)) return;
    if (dialog === 'folder') setFolderPrefix(`${pathInput}/`);
    if (dialog === 'create') { onCreate?.(pathInput); setFolderPrefix(''); }
    if (dialog === 'rename' && !protectedRoot) onRename?.(selectedPath, pathInput);
    setDialog(null);
  };
  return <Stack spacing={1} sx={{ minWidth: 0 }}>
    {!readOnly && <Stack direction="row" spacing={0.5} useFlexGap role="group" aria-label="File actions" sx={{ flexWrap: 'nowrap', '& > span': { display: 'inline-flex', flexShrink: 0 } }}>
      <Tooltip title={files.length >= LIMITS.MAX_FILES ? 'File limit reached.' : 'New file'}><span><IconButton size="small" color="inherit" aria-label="New file" onClick={() => openDialog('create')} disabled={!onCreate || files.length >= LIMITS.MAX_FILES}><Iconify icon="solar:file-add-linear" /></IconButton></span></Tooltip>
      <Tooltip title="New folder"><span><IconButton size="small" color="inherit" aria-label="New folder" onClick={() => openDialog('folder')} disabled={!onCreate}><Iconify icon="solar:folder-with-files-linear" /></IconButton></span></Tooltip>
      <Tooltip title={cannotChangeSelected ? protectedReason : 'Rename'}><span><IconButton size="small" color="inherit" aria-label="Rename" disabled={!selectedFile || !onRename || cannotChangeSelected} onClick={() => openDialog('rename')}><Iconify icon="solar:pen-linear" /></IconButton></span></Tooltip>
      <Tooltip title={cannotChangeSelected ? protectedReason : 'Delete'}><span><IconButton size="small" color="inherit" aria-label="Delete" disabled={!selectedFile || !onDelete || cannotChangeSelected} onClick={() => setDialog('delete')}><Iconify icon="solar:trash-bin-trash-linear" /></IconButton></span></Tooltip>
    </Stack>}
    {folderPrefix && !readOnly && <Typography variant="s3" color="text.secondary">New files will start in {folderPrefix}</Typography>}
    {!rows.length && <Typography variant="s3" color="text.secondary">No files yet.</Typography>}
    <Box role="tree" aria-label="Skill files">
      {rows.map((row, index) => <ButtonBase key={row.path} component="div" role="treeitem"
        ref={(element) => { if (element) refs.current.set(row.path, element); else refs.current.delete(row.path); }}
        tabIndex={row.path === activePath ? 0 : -1} aria-label={row.name} aria-level={row.level} aria-posinset={row.position} aria-setsize={row.setSize}
        aria-selected={row.path === selectedPath} aria-expanded={row.type === 'folder' ? !collapsed.has(row.path) : undefined}
        onFocus={() => setFocused(row.path)} onKeyDown={(event) => onKeyDown(event, row, index)} onClick={() => activate(row)}
        sx={{ display: 'flex', justifyContent: 'flex-start', gap: 1, width: '100%', minHeight: 40, pl: 1 + (row.level - 1) * 1.5, pr: 1, borderRadius: 1, bgcolor: row.path === selectedPath ? 'action.selected' : 'transparent', '&:hover': { bgcolor: 'action.hover' }, '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 } }}>
        {row.type === 'folder' && <Iconify icon={collapsed.has(row.path) ? 'solar:alt-arrow-right-linear' : 'solar:alt-arrow-down-linear'} />}
        <Typography variant="s1" sx={{ flex: 1, textAlign: 'left', overflowWrap: 'anywhere', fontWeight: pinned.includes(row.path) ? 500 : 400 }}>{row.name}</Typography>
        {row.status && <FileStatusBadge status={row.status} />}
      </ButtonBase>)}
    </Box>
    <ModalWrapper open={['create', 'folder', 'rename'].includes(dialog)} onClose={() => setDialog(null)}
      title={dialog === 'rename' ? 'Rename file' : dialog === 'folder' ? 'New folder' : 'New file'} actionBtnTitle={dialog === 'folder' ? 'Use prefix' : dialog === 'rename' ? 'Rename' : 'Create file'} isValid={!pathError} onSubmit={submitPath}>
      <TextField autoFocus size="small" label={dialog === 'folder' ? 'Folder path' : 'File path'} value={pathInput} onChange={(event) => setPathInput(event.target.value)} error={Boolean(pathInput && pathError)} helperText={pathInput && pathError ? pathError : dialog === 'folder' ? 'Folders are path prefixes. Add a file to keep this folder in the skill.' : 'Paths are relative to the skill, for example references/policy.md.'} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); submitPath(); } }} />
    </ModalWrapper>
    <ConfirmDialog open={dialog === 'delete'} onClose={() => setDialog(null)} title="Delete file?" content={`Remove ${selectedPath || 'this file'} from this draft? Published versions will not change.`}
      action={<Button variant="contained" color="error" disabled={!selectedFile || cannotChangeSelected} onClick={() => { if (selectedFile && !cannotChangeSelected) onDelete?.(selectedPath); setDialog(null); }}>Delete file</Button>} />
  </Stack>;
}
FileTree.propTypes = {
  files: PropTypes.arrayOf(PropTypes.shape({ path: PropTypes.string.isRequired, status: PropTypes.string })).isRequired,
  selectedPath: PropTypes.string, onSelect: PropTypes.func.isRequired, onCreate: PropTypes.func, onRename: PropTypes.func,
  onDelete: PropTypes.func, pinned: PropTypes.arrayOf(PropTypes.string), protectedPaths: PropTypes.arrayOf(PropTypes.string), isPathReadOnly: PropTypes.func, readOnly: PropTypes.bool,
};
