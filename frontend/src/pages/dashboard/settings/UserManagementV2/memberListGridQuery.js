import { DEFAULT_MEMBER_LIST_SORT } from "./getUserQueryOptions";

export const ORG_MEMBER_LIST_SORT_FIELDS = Object.freeze([
  "name",
  "email",
  "status",
  "type",
  "created_at",
  "date_joined",
  "org_level",
]);

export const WORKSPACE_MEMBER_LIST_SORT_FIELDS = Object.freeze([
  "name",
  "email",
  "status",
  "type",
  "created_at",
  "date_joined",
  "ws_level",
]);

export const gridSortModelToMemberListSort = (
  sortModel,
  { workspaceScope = false } = {},
) => {
  if (!Array.isArray(sortModel) || sortModel.length === 0) {
    return DEFAULT_MEMBER_LIST_SORT;
  }

  const firstSort = sortModel[0];
  const field = firstSort?.colId;
  const allowedFields = workspaceScope
    ? WORKSPACE_MEMBER_LIST_SORT_FIELDS
    : ORG_MEMBER_LIST_SORT_FIELDS;

  if (!allowedFields.includes(field)) {
    throw new TypeError(`Unsupported member list sort field: ${field}`);
  }

  return firstSort.sort === "desc" ? `-${field}` : field;
};
