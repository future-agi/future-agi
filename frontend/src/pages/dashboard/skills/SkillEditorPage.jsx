import { Helmet } from 'react-helmet-async';
import SkillEditorView from 'src/sections/skills/editor/SkillEditorView';

export default function SkillEditorPage() {
  return <><Helmet><title>Edit skill · Skills</title></Helmet><SkillEditorView /></>;
}
