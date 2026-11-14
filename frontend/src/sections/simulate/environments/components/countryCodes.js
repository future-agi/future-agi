import { countries } from "src/assets/data/countries";

// Flag emoji from an ISO-3166 alpha-2 code (regional-indicator pair).
const flagOf = (iso) =>
  iso && iso.length === 2
    ? String.fromCodePoint(...iso.toUpperCase().split("").map((c) => 0x1f1e6 + c.charCodeAt(0) - 65))
    : "";

/*
  Full country list from src/assets/data/countries. `suggested` markets
  (US/GB/IN/AU/DE/FR …) surface first, then the rest alphabetised, so the picker
  opens on the countries voice pilots actually land in without hiding the tail.
  `phone` uses "1-268" for shared-prefix territories; keep just the leading trunk
  so the picker never shows a hyphenated code the user cannot type.
*/
export const COUNTRY_OPTIONS = (() => {
  const cleaned = countries
    .filter((c) => c.code && c.phone)
    .map((c) => ({
      iso: c.code,
      name: c.label,
      dial: `+${String(c.phone).split("-")[0]}`,
      suggested: !!c.suggested,
      flag: flagOf(c.code),
    }));
  const suggested = cleaned.filter((c) => c.suggested);
  const rest = cleaned.filter((c) => !c.suggested).sort((a, b) => a.name.localeCompare(b.name));
  return [...suggested, ...rest];
})();

export const COUNTRY_BY_ISO = Object.fromEntries(COUNTRY_OPTIONS.map((c) => [c.iso, c]));
