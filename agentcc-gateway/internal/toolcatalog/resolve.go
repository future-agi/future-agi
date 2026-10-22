package toolcatalog

import (
	"errors"
	"fmt"
	"maps"
	"path"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var (
	// ErrDanglingPriceCard: a price_card reference names a card or SKU the
	// catalog does not have. A startup error.
	ErrDanglingPriceCard = errors.New("dangling tool price card reference")
	// ErrManagedNotAllowed: a managed credential or opt-in for a vendor whose
	// card is byok_only. A startup error in operator config;
	// 400 CodeManagedNotAllowed in an org config.
	ErrManagedNotAllowed = errors.New("managed use of a byok_only vendor")
)

// CodeManagedNotAllowed is the error code of ErrManagedNotAllowed in an org
// config refusal.
const CodeManagedNotAllowed = "capability_managed_not_allowed"

// ManagedServerID is a managed tool server's id as orgs see it, fagi.<id>,
// whether the operator wrote "web" or "fagi.web" (config validation checks
// the ids unique after stripping the prefix); managed-server lookups key by it.
func ManagedServerID(id string) string {
	return "fagi." + strings.TrimPrefix(id, "fagi.")
}

// ServerPrice is a managed tool server's resolved price card.
type ServerPrice struct {
	ServerID string // fagi.<id>
	Billing  string // config.ManagedServerBillingReserve or ...Delegated
	// Card and SKU are the referenced price; both nil for a delegated
	// server with no price_card.
	Card *Card
	SKU  *SKU
}

// PricedPerCall reports whether the server has no card of its own: a
// delegated server whose capability core prices each call at the card of
// the vendor it uses.
func (p ServerPrice) PricedPerCall() bool { return p.Card == nil }

// ResolveManagedServers resolves every mcp.managed_servers entry's
// price_card, keyed by ManagedServerID. A reference to a missing card or SKU
// is ErrDanglingPriceCard; a server that names a byok_only card is
// ErrManagedNotAllowed whatever its billing: FutureAGI never supplies its
// own credential for a byok_only vendor. A delegated server may name no card
// (it resolves with none and the
// capability core prices each call); one that names a card must name one
// that exists. Every bad entry is reported, in id order.
func (c *Catalog) ResolveManagedServers(servers map[string]config.ManagedMCPServerConfig) (map[string]ServerPrice, error) {
	out := make(map[string]ServerPrice, len(servers))
	var errs []error
	for _, key := range slices.Sorted(maps.Keys(servers)) {
		s := servers[key]
		id := ManagedServerID(key)
		p := ServerPrice{ServerID: id, Billing: s.Billing}
		if p.Billing == "" {
			p.Billing = config.ManagedServerBillingReserve
		}
		if s.PriceCard.IsZero() && p.Billing == config.ManagedServerBillingDelegated {
			out[id] = p
			continue
		}
		ref := s.PriceCard
		card, sku, err := c.Lookup(ref.Capability, ref.Vendor, ref.SKU)
		if err != nil {
			errs = append(errs, fmt.Errorf("mcp.managed_servers.%s (%s): price_card {%s, %s, %s}: %w",
				key, id, ref.Capability, ref.Vendor, ref.SKU, err))
			continue
		}
		if card.ResaleStatus == ResaleByokOnly {
			errs = append(errs, fmt.Errorf("mcp.managed_servers.%s (%s, %s) names the %s/%s card, which is byok_only: %w",
				key, id, p.Billing, ref.Capability, ref.Vendor, ErrManagedNotAllowed))
			continue
		}
		p.Card, p.SKU = &card, &sku
		out[id] = p
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	return out, nil
}

// CheckOperatorConfig refuses an operator provider marked managed: true
// whose id is the vendor of a byok_only card, since that vendor's terms bar
// FutureAGI from supplying the credential.
func (c *Catalog) CheckOperatorConfig(cfg *config.Config) error {
	if cfg == nil {
		return nil
	}
	var errs []error
	for _, id := range slices.Sorted(maps.Keys(cfg.Providers)) {
		if !cfg.Providers[id].Managed {
			continue
		}
		for _, card := range c.VendorCards(id) {
			if card.ResaleStatus == ResaleByokOnly {
				errs = append(errs, fmt.Errorf("providers.%s is managed, but the %s/%s card is byok_only: %w",
					id, card.Capability, card.Vendor, ErrManagedNotAllowed))
			}
		}
	}
	return errors.Join(errs...)
}

// CheckOrgConfig refuses an org config whose mcp.managed_tools.allow names a
// byok_only card: a <capability-glob>/<vendor-glob> entry that matches the
// card's capability and vendor under path.Match. A vendor glob with * or ?
// is a wildcard (search/*) and names no vendor: it admits nothing by itself,
// and the capability core never manages a byok_only vendor. Any other vendor
// glob, a literal or one spelled with a \ escape or a [...] class
// (search/br\ave, search/brav[e]), names every vendor it matches. Django's
// validate_mcp applies the same rule (_refuse_managed_byok_only).
func (c *Catalog) CheckOrgConfig(oc *tenant.OrgConfig) error {
	var errs []error
	for _, glob := range oc.MCPManagedTools().Allow {
		capGlob, vendorGlob, ok := strings.Cut(glob, "/")
		if !ok || strings.HasPrefix(capGlob, "fagi.") || strings.ContainsAny(vendorGlob, "*?") {
			continue
		}
		for _, k := range c.keys {
			card := c.cards[k]
			if card.ResaleStatus != ResaleByokOnly {
				continue
			}
			capOK, _ := path.Match(capGlob, card.Capability)
			vendorOK, _ := path.Match(vendorGlob, card.Vendor)
			if capOK && vendorOK {
				errs = append(errs, fmt.Errorf("mcp.managed_tools.allow %q names %s/%s, which is byok_only: %w",
					glob, card.Capability, card.Vendor, ErrManagedNotAllowed))
			}
		}
	}
	return errors.Join(errs...)
}

// LoadForConfig is the one startup call: it loads the catalog from
// tools.catalog_dir, or the seed cards when that is empty (or cfg is nil),
// refuses a managed operator provider for a byok_only vendor, and resolves
// every managed tool server's price_card. Its error fails startup. The
// Catalog it returns is the one price source: every pricing reader takes
// cards from it (or from the ServerPrice map), never from SeedCatalog,
// which ignores tools.catalog_dir.
func LoadForConfig(cfg *config.Config) (*Catalog, map[string]ServerPrice, error) {
	if cfg == nil {
		cfg = &config.Config{}
	}
	var cat *Catalog
	var err error
	if cfg.Tools.CatalogDir != "" {
		cat, err = LoadDir(cfg.Tools.CatalogDir)
	} else {
		cat, err = SeedCatalog()
	}
	if err != nil {
		return nil, nil, err
	}
	if err := cat.CheckOperatorConfig(cfg); err != nil {
		return nil, nil, fmt.Errorf("tool catalog: %w", err)
	}
	prices, err := cat.ResolveManagedServers(cfg.MCP.ManagedServers)
	if err != nil {
		return nil, nil, fmt.Errorf("tool catalog: %w", err)
	}
	return cat, prices, nil
}
