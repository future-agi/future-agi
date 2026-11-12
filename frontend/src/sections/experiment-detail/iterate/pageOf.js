// The page of a path: the path without its trailing tab (`/data`,
// `/matrix`, `/paths`, `/summary`). Pure (iterateStore re-exports it).
export const pageOf = (path) =>
  String(path ?? "").replace(/\/(data|matrix|paths|summary)\/?$/, "");
