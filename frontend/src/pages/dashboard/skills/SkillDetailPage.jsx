import { Helmet } from 'react-helmet-async';
import SkillDetailView from 'src/sections/skills/detail/SkillDetailView';

export default function SkillDetailPage() {
  return <><Helmet><title>Skill details · Skills</title></Helmet><SkillDetailView /></>;
}
