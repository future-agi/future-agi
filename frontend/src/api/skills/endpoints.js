import { apiPath } from 'src/api/contracts/api-surface';

// Kept inside this feature's ownership boundary. Like utils/axios endpoints,
// every path is checked against the generated backend contract, lazily at use.
export const endpoints = {
  list: () => apiPath('/model-hub/skills/'),
  templates: () => apiPath('/model-hub/skills/templates/'),
  checks: () => apiPath('/model-hub/skills/checks/'),
  publish: () => apiPath('/model-hub/skills/publish/'),
  resolve: () => apiPath('/model-hub/skills/resolve/'),
  download: () => apiPath('/model-hub/skills/download/'),
  importPreview: () => apiPath('/model-hub/skills/import/preview/'),
  import: () => apiPath('/model-hub/skills/import/'),
  detail: (skill_id) => apiPath('/model-hub/skills/{skill_id}/', { skill_id }),
  versions: (skill_id) => apiPath('/model-hub/skills/{skill_id}/versions/', { skill_id }),
  version: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/', { skill_id, number }),
  file: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/files/', { skill_id, number }),
  versionDownload: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/download/', { skill_id, number }),
  restore: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/restore/', { skill_id, number }),
  compare: (skill_id) => apiPath('/model-hub/skills/{skill_id}/compare/', { skill_id }),
  labels: (skill_id) => apiPath('/model-hub/skills/{skill_id}/labels/', { skill_id }),
  label: (skill_id, label) => apiPath('/model-hub/skills/{skill_id}/labels/{label}/', { skill_id, label }),
  labelHistory: (skill_id) => apiPath('/model-hub/skills/{skill_id}/labels/history/', { skill_id }),
  draft: (skill_id) => apiPath('/model-hub/skills/{skill_id}/draft/', { skill_id }),
  publishDraft: (skill_id) => apiPath('/model-hub/skills/{skill_id}/draft/publish/', { skill_id }),
  usage: (skill_id) => apiPath('/model-hub/skills/{skill_id}/usage/', { skill_id }),
};
