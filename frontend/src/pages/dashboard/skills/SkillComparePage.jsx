import { Helmet } from 'react-helmet-async';
import SkillCompareView from 'src/sections/skills/compare/SkillCompareView';

export default function SkillComparePage() {
  return <><Helmet><title>Compare versions · Skills</title></Helmet><SkillCompareView /></>;
}
