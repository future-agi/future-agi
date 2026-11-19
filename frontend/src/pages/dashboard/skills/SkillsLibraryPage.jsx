import { Helmet } from 'react-helmet-async';
import SkillsLibraryView from 'src/sections/skills/library/SkillsLibraryView';

export default function SkillsLibraryPage() {
  return <><Helmet><title>Skills</title></Helmet><SkillsLibraryView /></>;
}
