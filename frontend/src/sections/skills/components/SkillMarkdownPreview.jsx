import { useMemo } from 'react';
import PropTypes from 'prop-types';
import { Box, Link } from '@mui/material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { parseDocument } from 'yaml';
import { LIMITS } from '../constants';
import { resolveRelativePath } from '../utils/paths';
import FrontmatterTable from './FrontmatterTable';

function splitFrontmatter(content) {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/);
  if (!match || new TextEncoder().encode(match[1]).length > LIMITS.MAX_FRONTMATTER_BYTES) return { body: content };
  try {
    const doc = parseDocument(match[1], { schema: 'core' });
    if (doc.errors.length) return { body: content };
    // Do not expand aliases from untrusted instructions (including cycles).
    const frontmatter = doc.toJS({ maxAliasCount: 0 });
    if (!frontmatter || Array.isArray(frontmatter) || typeof frontmatter !== 'object') return { body: content };
    return { frontmatter, body: content.slice(match[0].length) };
  } catch { return { body: content }; }
}

function SkillLink({ href, children, currentPath, onOpenPath }) {
  if (/^(https?:|mailto:)/i.test(href || '')) {
    return <Link href={href} target="_blank" rel="noopener noreferrer" sx={{ overflowWrap: 'anywhere' }}>{children}</Link>;
  }
  const path = resolveRelativePath(currentPath, href);
  if (!path) return <span>{children}</span>;
  return <Link component="button" type="button" onClick={() => onOpenPath?.(path)} sx={{ typography: 'inherit', verticalAlign: 'baseline', textAlign: 'left', overflowWrap: 'anywhere' }}>{children}</Link>;
}
SkillLink.propTypes = { href: PropTypes.string, children: PropTypes.node, currentPath: PropTypes.string, onOpenPath: PropTypes.func };

// No raw HTML plugin: skill content cannot inject markup or load remote images.
function ImageDescription({ alt }) { return <span>{alt ? `[Image: ${alt}]` : '[Image]'}</span>; }
ImageDescription.propTypes = { alt: PropTypes.string };

export default function SkillMarkdownPreview({ content = '', currentPath = 'SKILL.md', onOpenPath }) {
  const { frontmatter, body } = useMemo(() => splitFrontmatter(content), [content]);
  return <Box sx={{ typography: 's1', color: 'text.primary', minWidth: 0, overflowWrap: 'anywhere', '& h1, & h2, & h3, & h4, & h5, & h6': { typography: 'm3', mt: 2, mb: 1 }, '& p': { my: 1 }, '& pre': { typography: 's1', fontFamily: 'monospace', bgcolor: 'background.neutral', p: 1.5, borderRadius: 1, overflowX: 'auto' }, '& code': { fontFamily: 'monospace' }, '& blockquote': { m: 0, pl: 1.5, borderLeft: 2, borderColor: 'divider', color: 'text.secondary' }, '& table': { width: '100%', borderCollapse: 'collapse' }, '& td, & th': { borderBottom: 1, borderColor: 'divider', p: 1, textAlign: 'left' }, '& th': { typography: 'm3' } }}>
    <FrontmatterTable frontmatter={frontmatter} />
    <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
      a: (props) => <SkillLink {...props} currentPath={currentPath} onOpenPath={onOpenPath} />,
      img: ImageDescription,
    }}>{body}</ReactMarkdown>
  </Box>;
}
SkillMarkdownPreview.propTypes = { content: PropTypes.string, currentPath: PropTypes.string, onOpenPath: PropTypes.func };
