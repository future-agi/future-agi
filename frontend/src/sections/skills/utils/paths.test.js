import { describe, expect, it } from 'vitest';
import { buildFileTree, languageForPath, resolveRelativePath } from './paths';

 describe('buildFileTree', () => {
  it('pins SKILL.md and nests sorted folders with file metadata', () => {
    const tree = buildFileTree([{ path: 'z.md' }, { path: 'references/z.md', status: 'modified' }, { path: 'SKILL.md' }, { path: 'references/a.md' }, { path: 'assets/images/readme.md' }]);
    expect(tree.map((node) => node.path)).toEqual(['SKILL.md', 'assets', 'references', 'z.md']);
    expect(tree[2].children.map((node) => node.name)).toEqual(['a.md', 'z.md']);
    expect(tree[2].children[1]).toMatchObject({ path: 'references/z.md', type: 'file', status: 'modified' });
    expect(tree[1].children[0].children[0].path).toBe('assets/images/readme.md');
  });
  it('returns an empty tree', () => expect(buildFileTree()).toEqual([]));
 });

describe('resolveRelativePath', () => {
  it.each([
    ['SKILL.md', 'references/policy.md', 'references/policy.md'],
    ['references/a.md', '../SKILL.md', 'SKILL.md'],
    ['references/deep/a.md', '../../SKILL.md#intro', 'SKILL.md'],
    ['references/a.md', './policy.md?view=raw#refunds', 'references/policy.md'],
    ['references/a.md', '#refunds', 'references/a.md'],
    ['SKILL.md', 'references/a%20b.md', 'references/a b.md'],
    ['SKILL.md', '../private.md', null],
    ['SKILL.md', '%2e%2e/private.md', null],
    ['SKILL.md', '/etc/passwd', null],
    ['SKILL.md', '//example.invalid/a', null],
    ['SKILL.md', 'https://example.invalid', null],
    ['SKILL.md', 'mailto:a@example.invalid', null],
    ['SKILL.md', ['javascript', 'alert(1)'].join(':'), null],
    ['SKILL.md', '%2fetc/passwd', null],
    ['SKILL.md', 'a%5cb', null],
    ['SKILL.md', '%00file', null],
    ['SKILL.md', '%zz', null],
  ])('resolves %s → %s safely', (from, href, expected) => expect(resolveRelativePath(from, href)).toBe(expected));
});

it.each([['SKILL.md', 'markdown'], ['script.py', 'python'], ['x.ts', 'typescript'], ['a.sh', 'shell'], ['a.yml', 'yaml'], ['a.unknown', 'plaintext']])('selects a language for %s', (path, language) => expect(languageForPath(path)).toBe(language));
