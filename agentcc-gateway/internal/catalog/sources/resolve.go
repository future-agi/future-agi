package sources

import (
	"fmt"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Resolve attaches to each managed provider's source what its config.yaml
// catalog_source reads it with:
//   - openrouter: nothing; the catalog package reads OpenRouter itself.
//   - openai_compatible: ParseOpenAICompatible over GET {base_url}/models,
//     its endpoints on upstream_slug (else the provider's type, else id),
//     starting from the snapshot bundled for that slug when there is one.
//   - price_card: the card (price_card_file, else the bundled
//     <provider_id>.json), re-read on every refresh and never fetched. The
//     card must load now, and its upstream_fee_bps must equal the
//     provider's effective upstream_fee_bps: the cost plugin's landed cost
//     reads the config and endpoint ranking reads the card, so a mismatch
//     fails startup rather than letting the two disagree.
func Resolve(cfg *config.Config, srcs []catalog.Source) ([]catalog.Source, error) {
	out := make([]catalog.Source, 0, len(srcs))
	for _, src := range srcs {
		pc := cfg.Providers[src.ProviderID]
		if pc.CatalogSource != "" {
			src.CatalogSource = pc.CatalogSource
		}
		switch src.Kind() {
		case catalog.SourceOpenAICompatible:
			slug := firstNonEmpty(pc.UpstreamSlug, strings.ToLower(src.Type), src.ProviderID)
			src.UpstreamSlug = slug
			src.Parse = func(body []byte) (map[string]*catalog.Model, error) { return ParseOpenAICompatible(body, slug) }
			src.Bundled = bundledLoader(bundledSnapshots, src.ProviderID, slug)
		case catalog.SourcePriceCard:
			load, slug, err := cardLoader(src.ProviderID, pc)
			if err != nil {
				return nil, err
			}
			src.UpstreamSlug, src.Load = slug, load
		}
		out = append(out, src)
	}
	return out, nil
}

// cardLoader checks providerID's card once and returns the loader that
// re-reads it, with the card's upstream slug.
func cardLoader(providerID string, pc config.ProviderConfig) (func(time.Time) (*catalog.Snapshot, error), string, error) {
	read := func() (*PriceCard, error) {
		card, err := LoadPriceCard(pc.PriceCardFile, providerID)
		if err != nil {
			return nil, err
		}
		if fee := pc.EffectiveUpstreamFeeBps(); card.UpstreamFeeBps != fee {
			return nil, fmt.Errorf("provider %q: its price card says upstream_fee_bps %d, but providers.%s.upstream_fee_bps is %d",
				providerID, card.UpstreamFeeBps, providerID, fee)
		}
		if pc.UpstreamSlug != "" && pc.UpstreamSlug != card.UpstreamSlug {
			return nil, fmt.Errorf("provider %q: its price card is for upstream_slug %q, but providers.%s.upstream_slug is %q",
				providerID, card.UpstreamSlug, providerID, pc.UpstreamSlug)
		}
		return card, nil
	}
	card, err := read()
	if err != nil {
		return nil, "", err
	}
	load := func(now time.Time) (*catalog.Snapshot, error) {
		c, err := read()
		if err != nil {
			return nil, err
		}
		return c.Snapshot(providerID, now), nil
	}
	return load, card.UpstreamSlug, nil
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
