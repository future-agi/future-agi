export const skillsKeys = {
  all: ['skills'],
  list: (params = {}) => ['skills', 'list', params],
  detail: (id) => ['skills', id, 'detail'],
  templates: () => ['skills', 'templates'],
  versions: (id, params = {}) => ['skills', id, 'versions', params],
  version: (id, number) => ['skills', id, 'version', number],
  file: (id, number, path) => ['skills', id, 'file', number, path],
  compare: (id, from, to) => ['skills', id, 'compare', from, to],
  labels: (id) => ['skills', id, 'labels'],
  labelHistory: (id, label) => ['skills', id, 'labelHistory', label || null],
  draft: (id) => ['skills', id, 'draft'],
  usage: (id, days) => ['skills', id, 'usage', days],
};
