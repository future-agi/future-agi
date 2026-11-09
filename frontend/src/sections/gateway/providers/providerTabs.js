// Providers page tabs by URL slug. The provider list used to be two tabs
// (health and config); both old URLs open the one list. Cache stays visible
// while off; its panel explains the current state without fetching figures.
export const PROVIDER_TABS = [
  { slug: "", label: "Providers" },
  { slug: "routing", label: "Routing" },
  { slug: "cache", label: "Cache" },
];
const LEGACY_LIST_SLUGS = new Set(["health", "config"]);

export function providerTabsFor() {
  return PROVIDER_TABS;
}

export function providerTabIndex(tabs, slug) {
  if (!slug || LEGACY_LIST_SLUGS.has(slug)) return 0;
  const index = tabs.findIndex((tab) => tab.slug === slug);
  return index >= 0 ? index : 0;
}

// Said under the tabs while the gateway runs no response cache.
export const CACHE_OFF_LINE = "Response caching is off on this gateway.";
