import PropTypes from "prop-types";
import SectionCard from "../components/SectionCard";
import PanelSourceRepo from "./PanelSourceRepo";
import PanelHostedPlatform from "./PanelHostedPlatform";
import PanelCodeUpload from "./PanelCodeUpload";
import { OPTIONS } from "../environmentOptions";

const PANELS = {
  source: PanelSourceRepo,
  hosted: PanelHostedPlatform,
  upload: PanelCodeUpload,
};

// Each panel runs its own inline preflight and stages the build ticket itself
// (usePanelBuild), so FlowPanel just frames the chosen source form.
export default function FlowPanel({ choice }) {
  const opt = OPTIONS.find((o) => o.id === choice);
  const Body = PANELS[choice];
  if (!opt || !Body) return null;
  return (
    <SectionCard title={opt.title} subtitle={opt.setupSubtitle || opt.blurb}>
      <Body />
    </SectionCard>
  );
}
FlowPanel.propTypes = { choice: PropTypes.string };
