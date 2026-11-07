import { describe, expect, it } from 'vitest';
import { paths } from 'src/routes/paths';
import { getContractedApiMethods } from 'src/api/contracts/api-surface';
import { endpoints } from './endpoints';

// Exercise the real generated registry, not the path-only stub used by transport tests.
const routes = [
  ['list', '', [], ['get', 'post']],
  ['templates', 'templates/', [], ['get']],
  ['checks', 'checks/', [], ['post']],
  ['publish', 'publish/', [], ['post']],
  ['resolve', 'resolve/', [], ['get']],
  ['download', 'download/', [], ['get']],
  ['importPreview', 'import/preview/', [], ['post']],
  ['import', 'import/', [], ['post']],
  ['detail', '{skill_id}/', ['skill-id'], ['get', 'patch', 'delete']],
  ['versions', '{skill_id}/versions/', ['skill-id'], ['get', 'post']],
  ['version', '{skill_id}/versions/{number}/', ['skill-id', 3], ['get']],
  ['file', '{skill_id}/versions/{number}/files/', ['skill-id', 3], ['get']],
  ['versionDownload', '{skill_id}/versions/{number}/download/', ['skill-id', 3], ['get']],
  ['restore', '{skill_id}/versions/{number}/restore/', ['skill-id', 3], ['post']],
  ['compare', '{skill_id}/compare/', ['skill-id'], ['get']],
  ['labels', '{skill_id}/labels/', ['skill-id'], ['get']],
  ['label', '{skill_id}/labels/{label}/', ['skill-id', 'production'], ['put', 'delete']],
  ['labelHistory', '{skill_id}/labels/history/', ['skill-id'], ['get']],
  ['draft', '{skill_id}/draft/', ['skill-id'], ['get', 'put', 'delete']],
  ['publishDraft', '{skill_id}/draft/publish/', ['skill-id'], ['post']],
  ['usage', '{skill_id}/usage/', ['skill-id'], ['get']],
];

describe('integrated Skills routes', () => {
  it.each(routes)('%s is registered with the required methods and parameters', (name, suffix, args, methods) => {
    const template = `/model-hub/skills/${suffix}`;
    expect(getContractedApiMethods(template).map((method) => method.toLowerCase())).toEqual(expect.arrayContaining(methods));
    expect(endpoints[name](...args)).toBe(template.replace('{skill_id}', 'skill-id').replace('{number}', '3').replace('{label}', 'production'));
  });

  it('keeps the library, editor, comparison and API-key links on their registered routes', () => {
    expect(paths.dashboard.skills.root).toBe('/dashboard/skills');
    expect(paths.dashboard.skills.detail('skill-id')).toBe('/dashboard/skills/skill-id');
    expect(paths.dashboard.skills.edit('skill-id')).toBe('/dashboard/skills/skill-id/edit');
    expect(paths.dashboard.skills.compare('skill-id')).toBe('/dashboard/skills/skill-id/compare');
    expect(paths.dashboard.settings.apiKeys).toBe('/dashboard/settings/api_keys');
  });
});
