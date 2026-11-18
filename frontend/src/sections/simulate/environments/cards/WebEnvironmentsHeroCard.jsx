import PropTypes from "prop-types";
import HeroCard from "./HeroCard";
import { OPTIONS, OPTION_ID, HERO_CLONES, HERO_COPY } from "../environmentOptions";

export default function WebEnvironmentsHeroCard({ onClick }) {
  const option = OPTIONS.find((o) => o.id === OPTION_ID.WEB);
  const copy = HERO_COPY.web;
  return (
    <HeroCard
      icon={option.icon}
      title={option.title}
      tag={copy.tag}
      description={copy.description}
      chips={HERO_CLONES}
      moreLabel={copy.moreLabel}
      onClick={onClick}
      comingSoon
    />
  );
}
WebEnvironmentsHeroCard.propTypes = { onClick: PropTypes.func };
