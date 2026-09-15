import PropTypes from "prop-types";
import SectionCard from "../components/SectionCard";
import PanelSourceRepo from "./PanelSourceRepo";
import PanelHostedPlatform from "./PanelHostedPlatform";
import PanelCodeUpload from "./PanelCodeUpload";
import useBuildHandoff from "../hooks/useBuildHandoff";
import { OPTIONS } from "../environmentOptions";

const PANELS = {
  source: PanelSourceRepo,
  hosted: PanelHostedPlatform,
  upload: PanelCodeUpload,
};

export default function FlowPanel({ choice }) {
  const handoff = useBuildHandoff();
  const opt = OPTIONS.find((o) => o.id === choice);
  const Body = PANELS[choice];
  if (!opt || !Body) return null;
  return (
    <SectionCard title={opt.title} subtitle={opt.setupSubtitle || opt.blurb}>
      <Body onBuild={handoff} />
    </SectionCard>
  );
}
FlowPanel.propTypes = { choice: PropTypes.string };
