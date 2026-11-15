import { useEffect, useId, useMemo, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Autocomplete, Box, Button, ButtonBase, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import ModalWrapper from 'src/components/ModalWrapper/ModalWrapper';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { buildFileTree, hasControlCharacters } from '../utils/paths';
import { LIMITS } from '../constants';
import FileStatusBadge from './FileStatusBadge';
import './skillIcons';

const DEFAULT_PINNED = ['SKILL.md'];
// Files the platform adds so apps load the right versions. Their paths stay listed under this group.
const OTHER_FILES_NOTE = 'Added by Future AGI so apps load the right versions.';
// The platform's context descriptor reads as settings, not as a file to edit. Its path stays in the title and the description.
// Generated folders start closed unless the caller says which folders to close (collapsedFolders), as the draft editor does.
const CONTEXT_SETTINGS_LABEL = 'Context settings (generated)';
const plainPath = (path) => path.replace(/^group:[a-z]+\//, '');

export default function FileTree({ files, selectedPath, onSelect, onCreate, onRename, onDelete, pinned = DEFAULT_PINNED, protectedPaths = DEFAULT_PINNED, isPathReadOnly = () => false, readOnly = true, generatedFolders = [], collapsedFolders }) {
  const noteId = useId();
  const [collapsed, setCollapsed] = useState(() => new Set([...(collapsedFolders ?? generatedFolders), 'group:linked', 'group:generated']));
  const [focused, setFocused] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [pathInput, setPathInput] = useState('');
  const folders = [...new Set(files.flatMap((file) => file.path.split('/').slice(0, -1).map((_, index, parts) => `${parts.slice(0, index + 1).join('/')}/`)))];
  const refs = useRef(new Map());
  useEffect(() => {
    // Preview links may select a file inside a collapsed ancestor.
    const parts = (selectedPath || '').split('/');
    setCollapsed((previous) => {
      const ancestors = parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
      if (files.some((file) => file.origin?.kind)) {
        const kind = files.find((file) => file.path === selectedPath)?.origin?.kind;
        const group = `group:${['linked', 'generated'].includes(kind) ? kind : 'owned'}`;
        ancestors.push(...ancestors.map((path) => `${group}/${path}`), group);
      }
      if (!ancestors.some((path) => previous.has(path))) return previous;
      return new Set([...previous].filter((path) => !ancestors.includes(path)));
    });
  }, [selectedPath, files]);
  const rows = useMemo(() => {
    const grouped = files.some((file) => file.origin?.kind);
    const groupTree = (kind, name) => {
      const groupPath = `group:${kind}`;
      const prefixFolders = (nodes) => nodes.map((node) => node.children ? { ...node, path: `${groupPath}/${node.path}`, children: prefixFolders(node.children) } : node);
      const items = files.filter((file) => (['linked', 'generated'].includes(file.origin?.kind) ? file.origin.kind : 'owned') === kind);
      return { name, path: groupPath, type: 'folder', children: prefixFolders(buildFileTree(items)) };
    };
    const tree = grouped ? [['owned', 'Instructions'], ['linked', 'Linked skills'], ['generated', 'Other files']].map(([kind, name]) => groupTree(kind, name)).filter((group) => group.children.length) : buildFileTree(files);
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
  const isContextSettings = (row) => row.type !== 'folder' && generatedFolders.some((folder) => plainPath(row.path) === `${folder.replace(/\/$/, '')}/context.json`);
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
    setPathInput(kind === 'rename' ? selectedPath : '');
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
      return existing === lower || lower.startsWith(`${existing}/`) || existing.startsWith(`${lower}/`);
    })) return 'That path is already used by a file or folder.';
    return '';
  })();
  const submitPath = () => {
    if (pathError || readOnly || (dialog === 'rename' && cannotChangeSelected)) return;
    if (dialog === 'create') onCreate?.(pathInput);
    if (dialog === 'rename' && !protectedRoot) onRename?.(selectedPath, pathInput);
    setDialog(null);
  };
  return <Stack spacing={1} sx={{ minWidth: 0 }}>
    {!readOnly && <Stack direction="row" spacing={0.5} useFlexGap role="group" aria-label="File actions" sx={{ flexWrap: 'nowrap', '& > span': { display: 'inline-flex', flexShrink: 0 } }}>
      <Tooltip title={files.length >= LIMITS.MAX_FILES ? 'File limit reached.' : 'Add file'}><span><IconButton size="small" color="inherit" aria-label="Add file" onClick={() => openDialog('create')} disabled={!onCreate || files.length >= LIMITS.MAX_FILES}><Iconify icon="solar:file-add-linear" /></IconButton></span></Tooltip>
      <Tooltip title={cannotChangeSelected ? protectedReason : 'Rename file'}><span><IconButton size="small" color="inherit" aria-label="Rename file" disabled={!selectedFile || !onRename || cannotChangeSelected} onClick={() => openDialog('rename')}><Iconify icon="solar:pen-linear" /></IconButton></span></Tooltip>
      <Tooltip title={cannotChangeSelected ? protectedReason : 'Delete file'}><span><IconButton size="small" color="inherit" aria-label="Delete file" disabled={!selectedFile || !onDelete || cannotChangeSelected} onClick={() => setDialog('delete')}><Iconify icon="solar:trash-bin-trash-linear" /></IconButton></span></Tooltip>
    </Stack>}
    {!rows.length && <Typography variant="s3" color="text.secondary">No files yet.</Typography>}
    <Box role="tree" aria-label="Skill files">
      {rows.map((row, index) => <ButtonBase key={row.path} component="div" role="treeitem"
        ref={(element) => { if (element) refs.current.set(row.path, element); else refs.current.delete(row.path); }}
        tabIndex={row.path === activePath ? 0 : -1} aria-label={row.name} aria-describedby={row.path === 'group:generated' ? noteId : undefined}
        {...(isContextSettings(row) ? { title: plainPath(row.path), 'aria-description': plainPath(row.path) } : {})} aria-level={row.level} aria-posinset={row.position} aria-setsize={row.setSize}
        aria-selected={row.path === selectedPath} aria-expanded={row.type === 'folder' ? !collapsed.has(row.path) : undefined}
        onFocus={() => setFocused(row.path)} onKeyDown={(event) => onKeyDown(event, row, index)} onClick={() => activate(row)}
        sx={{ display: 'flex', justifyContent: 'flex-start', gap: 1, width: '100%', minHeight: 32, pl: 1 + (row.level - 1) * 1.5, pr: 1, borderRadius: 1, bgcolor: row.path === selectedPath ? 'action.selected' : 'transparent', '&:hover': { bgcolor: 'action.hover' }, '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 } }}>
        <Box sx={{ width: 16, flexShrink: 0, display: 'flex' }}>{row.type === 'folder' && <Iconify width={16} icon={collapsed.has(row.path) ? 'solar:alt-arrow-right-linear' : 'solar:alt-arrow-down-linear'} />}</Box>
        {row.path === 'group:generated'
          ? <Box sx={{ flex: 1, minWidth: 0, textAlign: 'left', py: 0.5 }}>
            <Typography variant="s1" sx={{ display: 'block', overflowWrap: 'anywhere' }}>{row.name}</Typography>
            <Typography id={noteId} variant="s3" color="text.secondary" sx={{ display: 'block' }}>{OTHER_FILES_NOTE}</Typography>
          </Box>
          : <Typography variant="s1" sx={{ flex: 1, textAlign: 'left', overflowWrap: 'anywhere', fontWeight: pinned.includes(row.path) ? 500 : 400 }}>{isContextSettings(row) ? CONTEXT_SETTINGS_LABEL : row.name}</Typography>}
        {generatedFolders.includes(row.path) && <Typography variant="s3" color="text.secondary">Generated</Typography>}
        {row.status && <FileStatusBadge status={row.status} />}
      </ButtonBase>)}
    </Box>
    <ModalWrapper open={['create', 'rename'].includes(dialog)} onClose={() => setDialog(null)}
      title={dialog === 'rename' ? 'Rename file' : 'Add file'} actionBtnTitle={dialog === 'rename' ? 'Rename' : 'Create file'} isValid={!pathError} onSubmit={submitPath}>
      <Autocomplete freeSolo forcePopupIcon={dialog === 'create' && folders.length > 0} options={dialog === 'create' ? folders : []}
        popupIcon={<Iconify icon="solar:alt-arrow-down-linear" />} inputValue={pathInput}
        onInputChange={(_, value) => setPathInput(value)}
        renderInput={(params) => <TextField {...params} autoFocus size="small" label="File path"
          error={Boolean(pathInput && pathError)} helperText={pathInput && pathError ? pathError : 'Folders are created from file paths. For example, references/policy.md.'} />} />
    </ModalWrapper>
    <ConfirmDialog open={dialog === 'delete'} onClose={() => setDialog(null)} title="Delete file?" content={`Remove ${selectedPath || 'this file'} from this draft? Published versions will not change.`}
      action={<Button variant="contained" color="error" disabled={!selectedFile || cannotChangeSelected} onClick={() => { if (selectedFile && !cannotChangeSelected) onDelete?.(selectedPath); setDialog(null); }}>Delete file</Button>} />
  </Stack>;
}
FileTree.propTypes = {
  files: PropTypes.arrayOf(PropTypes.shape({ path: PropTypes.string.isRequired, status: PropTypes.string })).isRequired,
  generatedFolders: PropTypes.arrayOf(PropTypes.string), collapsedFolders: PropTypes.arrayOf(PropTypes.string),
  selectedPath: PropTypes.string, onSelect: PropTypes.func.isRequired, onCreate: PropTypes.func, onRename: PropTypes.func,
  onDelete: PropTypes.func, pinned: PropTypes.arrayOf(PropTypes.string), protectedPaths: PropTypes.arrayOf(PropTypes.string), isPathReadOnly: PropTypes.func, readOnly: PropTypes.bool,
};
