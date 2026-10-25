package sources

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/catalog/pricecards"
)

// StaleAfter is how old a price card may be (from its as_of date) before its
// models are marked stale_price and drop out of managed eligibility.
const StaleAfter = 90 * 24 * time.Hour

// PriceCard is a direct provider's list prices: the catalog of a managed
// provider whose catalog_source is price_card (catalog/pricecards/
// <provider_id>.json, or the provider's price_card_file).
type PriceCard struct {
	// Provider is the gateway provider id the card prices.
	Provider string `json:"provider"`
	// UpstreamSlug names the upstream, its endpoints' upstream_provider.
	UpstreamSlug string `json:"upstream_slug"`
	// UpstreamFeeBps is what the upstream adds on top of these prices; it
	// must equal the provider's effective upstream_fee_bps (Resolve).
	UpstreamFeeBps int `json:"upstream_fee_bps"`
	// AsOf is the day ("2026-10-01") the prices were read from SourceURL.
	AsOf      string `json:"as_of"`
	SourceURL string `json:"source_url"`
	// Notes say where the prices came from and what is left to confirm.
	Notes string `json:"notes,omitempty"`
	// DataCollection (allow, deny or unknown, the default), ZDR and Regions
	// are the account's data terms, on every endpoint of the card.
	DataCollection string          `json:"data_collection,omitempty"`
	ZDR            *bool           `json:"zdr,omitempty"`
	Regions        []string        `json:"regions,omitempty"`
	Models         []PriceCardItem `json:"models"`

	asOf time.Time
}

// PriceCardItem is one model of a price card.
type PriceCardItem struct {
	// ID is the provider's own model id, the one sent upstream.
	ID string `json:"id"`
	// Canonical is the model's canonical id; empty is derived
	// (catalog.Canonical).
	Canonical           string      `json:"canonical,omitempty"`
	Name                string      `json:"name,omitempty"`
	ContextLength       int         `json:"context_length,omitempty"`
	MaxOutput           int         `json:"max_output,omitempty"`
	Prices              *CardPrices `json:"prices,omitempty"`
	SupportedParameters []string    `json:"supported_parameters,omitempty"`
	Modalities          *Modalities `json:"modalities,omitempty"`
}

// CardPrices are a card model's prices in USD per token (prompt, completion,
// cache_read, cache_write) or per unit (request, image), as decimal strings
// or numbers. A model with no prices object, or with prices that cannot bill
// a call by themselves (no prompt, completion or request price above zero),
// is listed unpriced: a direct upstream reports no cost to bill it by.
type CardPrices struct {
	Prompt     price `json:"prompt"`
	Completion price `json:"completion"`
	CacheRead  price `json:"cache_read"`
	CacheWrite price `json:"cache_write"`
	Request    price `json:"request"`
	Image      price `json:"image"`
}

// Modalities are what a card model takes in and puts out.
type Modalities struct {
	Input  []string `json:"input"`
	Output []string `json:"output"`
}

// price is a non-negative price read from a decimal string or a number.
type price float64

func (p *price) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return fmt.Errorf("price %s is not a number", b)
	}
	if v < 0 {
		return fmt.Errorf("price %s is negative", b)
	}
	*p = price(v)
	return nil
}

// ParsePriceCard reads and checks a price card.
func ParsePriceCard(body []byte) (*PriceCard, error) {
	var c PriceCard
	if err := json.Unmarshal(body, &c); err != nil {
		return nil, fmt.Errorf("parsing price card: %w", err)
	}
	if err := c.validate(); err != nil {
		return nil, fmt.Errorf("price card %q: %w", c.Provider, err)
	}
	return &c, nil
}

func (c *PriceCard) validate() error {
	var err error
	switch {
	case c.UpstreamSlug == "":
		return errors.New("upstream_slug is required")
	case c.UpstreamFeeBps < 0:
		return errors.New("upstream_fee_bps must not be negative")
	case len(c.Models) == 0:
		return errors.New("lists no models")
	}
	if c.asOf, err = time.Parse(time.DateOnly, c.AsOf); err != nil {
		return fmt.Errorf("as_of must be a date (2026-10-01); got %q", c.AsOf)
	}
	switch c.DataCollection {
	case "", catalog.DataCollectionAllow, catalog.DataCollectionDeny, catalog.DataCollectionUnknown:
	default:
		return fmt.Errorf("data_collection must be allow, deny or unknown; got %q", c.DataCollection)
	}
	seen := make(map[string]bool, len(c.Models))
	for i, m := range c.Models {
		switch {
		case m.ID == "":
			return fmt.Errorf("models[%d] has no id", i)
		case seen[m.ID]:
			return fmt.Errorf("model %q is listed twice", m.ID)
		case m.Canonical != "" && strings.Count(m.Canonical, "/") != 1:
			return fmt.Errorf("model %q: canonical %q is not vendor/model", m.ID, m.Canonical)
		}
		seen[m.ID] = true
	}
	return nil
}

// LoadPriceCard reads providerID's card from path, or, when path is empty,
// the card bundled for providerID. The card must name providerID.
func LoadPriceCard(path, providerID string) (*PriceCard, error) {
	var body []byte
	var err error
	if path == "" {
		body, err = fs.ReadFile(pricecards.FS, providerID+".json")
		if errors.Is(err, fs.ErrNotExist) {
			return nil, fmt.Errorf("provider %q: no price_card_file and no bundled card %s.json", providerID, providerID)
		}
	} else {
		body, err = os.ReadFile(path)
	}
	if err != nil {
		return nil, fmt.Errorf("provider %q: reading its price card: %w", providerID, err)
	}
	card, err := ParsePriceCard(body)
	if err != nil {
		return nil, err
	}
	if card.Provider != providerID {
		return nil, fmt.Errorf("provider %q: its price card is for provider %q", providerID, card.Provider)
	}
	return card, nil
}

// Stale reports whether the card is too old to bill by at now.
func (c *PriceCard) Stale(now time.Time) bool { return now.Sub(c.asOf) > StaleAfter }

// Snapshot is the card as providerID's catalog at now. A model with prices
// that bill a call is priced and has one endpoint on the card's upstream,
// unless the card is stale: its models then keep their prices for display,
// marked stale_price, with no endpoint. Any other model is listed unpriced.
// The fetch time is now, the time the card was read; its own age is the
// as_of date.
func (c *PriceCard) Snapshot(providerID string, now time.Time) *catalog.Snapshot {
	stale := c.Stale(now)
	collection := c.DataCollection
	if collection == "" {
		collection = catalog.DataCollectionUnknown
	}
	snap := &catalog.Snapshot{ProviderID: providerID, Source: catalog.OriginPriceCard,
		CatalogSource: catalog.SourcePriceCard, FetchedAt: now, Models: make(map[string]*catalog.Model, len(c.Models))}
	for _, item := range c.Models {
		m := &catalog.Model{ID: item.ID, Name: item.Name, Canonical: strings.ToLower(item.Canonical),
			ContextLength: item.ContextLength, MaxOutputTokens: item.MaxOutput, SupportedParameters: item.SupportedParameters}
		if item.Modalities != nil {
			m.InputModalities, m.OutputModalities = item.Modalities.Input, item.Modalities.Output
		}
		snap.Models[item.ID] = m
		if item.Prices == nil || !item.Prices.catalogPrices().BillsCall() {
			m.Unpriced = true
			continue
		}
		m.Prices = item.Prices.catalogPrices()
		if stale {
			m.StalePrice = true
			continue
		}
		m.Endpoints = []catalog.Endpoint{{
			ID:               catalog.EndpointID(catalog.SourcePriceCard, c.UpstreamSlug, item.ID),
			Name:             item.Name,
			Source:           catalog.SourcePriceCard,
			UpstreamProvider: c.UpstreamSlug,
			UpstreamFeeBps:   c.UpstreamFeeBps,
			Tag:              c.UpstreamSlug,
			Regions:          c.Regions,
			ContextLength:    item.ContextLength,
			MaxCompletion:    item.MaxOutput,
			Prices:           m.Prices,
			SupportedParams:  item.SupportedParameters,
			ZDR:              c.ZDR,
			DataCollection:   collection,
		}}
	}
	return snap
}

func (p *CardPrices) catalogPrices() catalog.Prices {
	return catalog.Prices{
		Prompt: float64(p.Prompt), Completion: float64(p.Completion), CacheRead: float64(p.CacheRead),
		CacheWrite: float64(p.CacheWrite), Request: float64(p.Request), Image: float64(p.Image),
	}
}
