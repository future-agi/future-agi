import { useEffect } from "react";
import PropTypes from "prop-types";
import { useMonaco } from "@monaco-editor/react";
import { useForm } from "react-hook-form";

import { FormCodeEditor } from "src/components/form-code-editor";
import { languageForPath } from "src/sections/skills/utils/paths";

export default function SkillFileEditor({ file, onChange, target, readOnly = false }) {
  const { control, setValue } = useForm({ defaultValues: { content: file.content } });
  const monaco = useMonaco();
  useEffect(() => { setValue("content", file.content); }, [file.content, setValue]);

  useEffect(() => {
    if (!monaco || !target?.line) return undefined;
    let active = true;
    let focused = false;
    let boundEditor;
    let layoutSubscription;
    const focus = () => {
      if (!active || focused) return;
      const editor = monaco.editor.getEditors().find(
        (candidate) => candidate.getModel()?.uri.toString() === monaco.Uri.parse(file.path).toString(),
      );
      if (!editor) return;
      if (boundEditor !== editor) {
        layoutSubscription?.dispose();
        boundEditor = editor;
        // Document/Preview keep Monaco mounted to preserve undo history. Its
        // automaticLayout observer notifies us when the hidden pane is revealed.
        layoutSubscription = editor.onDidLayoutChange?.(focus);
      }
      const node = editor.getDomNode?.();
      if (node && node.getClientRects().length === 0) return;
      focused = true;
      editor.setPosition({ lineNumber: target.line, column: 1 });
      editor.revealLineInCenter(target.line);
      editor.focus();
    };
    focus();
    const subscription = monaco.editor.onDidCreateEditor(() => { Promise.resolve().then(focus); });
    return () => { active = false; subscription.dispose(); layoutSubscription?.dispose(); };
  }, [monaco, file.path, target]);

  return (
    <FormCodeEditor
      control={control}
      fieldName="content"
      label={file.path}
      path={file.path}
      value={file.content}
      onChange={(value) => { setValue("content", value ?? ""); onChange(value ?? ""); }}
      readOnly={readOnly}
      language={languageForPath(file.path)}
      height="100%"
      sx={{ flex: 1, minHeight: 0, '& > .MuiBox-root': { flex: 1, minHeight: 0 } }}
      options={{ readOnly, domReadOnly: readOnly, automaticLayout: true, minimap: { enabled: false }, wordWrap: "on", scrollBeyondLastLine: false }}
    />
  );
}

SkillFileEditor.propTypes = {
  file: PropTypes.shape({ path: PropTypes.string.isRequired, content: PropTypes.string.isRequired }).isRequired,
  onChange: PropTypes.func.isRequired,
  target: PropTypes.shape({ line: PropTypes.number }),
  readOnly: PropTypes.bool,
};
