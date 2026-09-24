import PropTypes from "prop-types";
import HeroCard from "./HeroCard";
import { OPTIONS, OPTION_ID, HERO_TEMPLATES, HERO_COPY } from "../environmentOptions";

export default function TemplateHeroCard({ selected, onClick }) {
  const option = OPTIONS.find((o) => o.id === OPTION_ID.TEMPLATES);
  const copy = HERO_COPY.templates;
  return (
    <HeroCard
      icon={option.icon}
      title={option.title}
      tag={copy.tag}
      description={copy.description}
      chips={HERO_TEMPLATES}
      moreLabel={copy.moreLabel}
      selected={selected}
      onClick={onClick}
    />
  );
}
TemplateHeroCard.propTypes = { selected: PropTypes.bool, onClick: PropTypes.func };
