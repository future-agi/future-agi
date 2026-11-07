// A tree node has {name, path, type: 'file'|'folder', children?, status?}.
export function buildFileTree(files = []) {
  const roots = [];
  const nodes = new Map();
  for (const file of files) {
    const parts = file.path.split('/');
    let children = roots;
    parts.forEach((name, index) => {
      const path = parts.slice(0, index + 1).join('/');
      let node = nodes.get(path);
      if (!node) {
        const isFile = index === parts.length - 1;
        node = isFile ? { ...file, name, type: 'file' } : { name, path, type: 'folder', children: [] };
        nodes.set(path, node);
        children.push(node);
      }
      children = node.children || [];
    });
  }
  const sort = (items) => {
    items.sort((a, b) => {
      if (a.path === 'SKILL.md') return -1;
      if (b.path === 'SKILL.md') return 1;
      if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    items.forEach((node) => node.children && sort(node.children));
    return items;
  };
  return sort(roots);
}

export const hasControlCharacters = (text) => [...text].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);

// Returns a bundle path, never a URL or a path outside the bundle. Anchors
// and query strings identify locations in the document, not separate files.
export function resolveRelativePath(fromPath, href) {
  if (!href || /^[a-z][a-z\d+.-]*:/i.test(href) || href.startsWith('/')) return null;
  const raw = href.split(/[?#]/)[0];
  let relative;
  try { relative = decodeURIComponent(raw); } catch { return null; }
  if (/^[a-z][a-z\d+.-]*:/i.test(relative) || relative.startsWith('/') || relative.includes('\\') || hasControlCharacters(relative)) return null;
  if (!relative) return fromPath || null;
  const parts = (fromPath || '').split('/').slice(0, -1);
  for (const part of relative.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.join('/') || null;
}

export function languageForPath(path = '') {
  const extension = path.split('.').pop().toLowerCase();
  return ({ md: 'markdown', mdx: 'markdown', py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', json: 'json', yaml: 'yaml', yml: 'yaml', sh: 'shell', bash: 'shell', rb: 'ruby', ps1: 'powershell', html: 'html', css: 'css', sql: 'sql', toml: 'ini' })[extension] || 'plaintext';
}
