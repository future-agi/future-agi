// HO D4: AG Grid column definitions of the optional Row info columns,
// made with the grid's own `system(field, headerName, extra)` factory.
import { AssignedCell, CommentsCell, TagsCell } from "./CollabCells";
import { COLLAB_COLUMN } from "./collabColumns";

/**
 * Column definitions for the optional columns the catalog offers, made with
 * the grid's own `system(field, headerName, extra)` factory.
 * @param {Function} system
 * @param {string[]} ids  catalog ids offered (collabCatalogEntries)
 */
export function collabSystemColumns(system, ids = []) {
  const defs = {
    [COLLAB_COLUMN.comments]: () =>
      system(COLLAB_COLUMN.comments, "Comments", {
        width: 150,
        sortable: false,
        cellRenderer: CommentsCell,
      }),
    [COLLAB_COLUMN.assigned]: () =>
      system(COLLAB_COLUMN.assigned, "Assigned to", {
        width: 160,
        sortable: false,
        cellRenderer: AssignedCell,
      }),
    [COLLAB_COLUMN.tags]: () =>
      system(COLLAB_COLUMN.tags, "Tags", {
        width: 200,
        sortable: false,
        cellRenderer: TagsCell,
      }),
  };
  return ids.filter((id) => defs[id]).map((id) => defs[id]());
}
