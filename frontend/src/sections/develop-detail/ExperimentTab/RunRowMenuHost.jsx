import React, { useEffect } from "react";
import PropTypes from "prop-types";
import RunRowMenu from "./RunRowMenu";

/** AG Grid may replace a grouped cell on a poll. The open menu belongs to the
 * grid, not that disposable cell. Resolve its IDs against current rows so run
 * status and permission checks stay live; the action implementation is shared.
 */
export default function RunRowMenuHost({ menu, rows, context, onClose }) {
  const runs = menu?.group
    ? rows.filter((run) => run.experiment_id === menu.experimentId)
    : menu
      ? menu.runIds
          .map((id) => rows.find((run) => run.run_id === id))
          .filter(Boolean)
      : [];
  const groupGone = menu?.group && context.group !== "experiment";
  useEffect(() => {
    if (menu && (!runs.length || groupGone)) onClose();
  }, [menu, runs.length, groupGone, onClose]);
  if (!menu || !runs.length || groupGone) return null;
  return (
    <RunRowMenu
      node={{
        group: menu.group,
        allLeafChildren: runs.map((data) => ({ data })),
      }}
      data={runs[0]}
      context={context}
      hostMenu={menu}
      onHostClose={onClose}
    />
  );
}

RunRowMenuHost.propTypes = {
  menu: PropTypes.object,
  rows: PropTypes.array.isRequired,
  context: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};
