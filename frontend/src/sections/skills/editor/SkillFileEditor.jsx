import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { loader, useMonaco } from "@monaco-editor/react";
import { Box } from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useForm } from "react-hook-form";

import { FormCodeEditor } from "src/components/form-code-editor";
import { languageForPath } from "src/sections/skills/utils/paths";

// `minHeight` (px) is a floor, not a size: without it the editor only fills the pane it is given.
export default function SkillFileEditor({ file, onChange, target, readOnly = false, minHeight }) {
  const { control, setValue } = useForm({ defaultValues: { content: file.content } });
  const monaco = useMonaco();
  const [loadError, setLoadError] = useState(null);
  useEffect(() => {
    let active = true;
    setLoadError(null);
    loader.init().catch((error) => { if (active) setLoadError(error); });
    return () => { active = false; };
  }, []);
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

  if (loadError) return <ErrorState title="Couldn't load the editor." detail="Save your changes before retrying. Retry reloads this page to download the editor again." onRetry={() => window.location.reload()} />;
  return (
    <FormCodeEditor
      loading={<Box sx={{ width: 1, height: 1, minHeight: 240, bgcolor: 'background.paper', p: 1.5 }}><ContentSkeleton variant="panel" label="Loading editor…" /></Box>}
      control={control}
      fieldName="content"
      label={file.path}
      path={file.path}
      value={file.content}
      onChange={(value) => { setValue("content", value ?? ""); onChange(value ?? ""); }}
      readOnly={readOnly}
      language={languageForPath(file.path)}
      height="100%"
      sx={{ flex: 1, minHeight: minHeight || 0, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden', '& > .MuiTypography-root': { px: 1.5, py: 1 }, '& > .MuiBox-root': { flex: 1, minHeight: 0 } }}
      options={{ renderLineHighlight: "none", readOnly, domReadOnly: readOnly, automaticLayout: true, minimap: { enabled: false }, wordWrap: "on", scrollBeyondLastLine: false }}
    />
  );
}

SkillFileEditor.propTypes = {
  file: PropTypes.shape({ path: PropTypes.string.isRequired, content: PropTypes.string.isRequired }).isRequired,
  onChange: PropTypes.func.isRequired,
  target: PropTypes.shape({ line: PropTypes.number }),
  readOnly: PropTypes.bool,
  minHeight: PropTypes.number,
};
