import PropTypes from "prop-types";
import { usePrebuiltEnvironments } from "src/api/simulate-environments/prebuilt";
import HeroCard from "./HeroCard";
import { OPTIONS, OPTION_ID, HERO_COPY } from "../environmentOptions";

// The library's own names, a few of them, so the card names what you will find.
const SHOWN_CHIPS = 4;

export default function TemplateHeroCard({ selected, onClick }) {
  const option = OPTIONS.find((o) => o.id === OPTION_ID.TEMPLATES);
  const copy = HERO_COPY.templates;
  const { data } = usePrebuiltEnvironments();
  const names = (data ?? []).map((template) => template.name);
  const hidden = names.length - SHOWN_CHIPS;
  return (
    <HeroCard
      icon={option.icon}
      title={option.title}
      tag={copy.tag}
      description={copy.description}
      chips={names.slice(0, SHOWN_CHIPS)}
      moreLabel={hidden > 0 ? `+ ${hidden} more` : undefined}
      selected={selected}
      onClick={onClick}
    />
  );
}
TemplateHeroCard.propTypes = { selected: PropTypes.bool, onClick: PropTypes.func };
