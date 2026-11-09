// W7 A0: Evals › Create for a typed eval type (Decision tree, Remote
// scorer). EvalCreatePage mounts it when isTypedEvalType(evalType) and shows
// a type's tab only when its editor is real (typedViews.typedTabsOf).
import React from "react";
import PropTypes from "prop-types";
import { TYPED_EDITORS, hasTypedEditor } from "./typedViews";

export default function TypedEvalEditor({ evalType, name, onSave, saving }) {
  const Editor = TYPED_EDITORS[evalType];
  if (!hasTypedEditor(evalType)) return null;
  return <Editor name={name} onSave={onSave} saving={saving} />;
}

TypedEvalEditor.propTypes = {
  evalType: PropTypes.string,
  name: PropTypes.string,
  onSave: PropTypes.func,
  saving: PropTypes.bool,
};
