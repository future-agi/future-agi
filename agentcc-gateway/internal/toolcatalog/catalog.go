package toolcatalog

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"slices"
	"strings"
	"sync"

	seedcards "github.com/futureagi/agentcc-gateway/catalog/tools"
)

// Catalog is a loaded, validated set of cards, one per capability and
// vendor. It is safe for concurrent reads.
type Catalog struct {
	cards map[cardKey]*Card
	keys  []cardKey // sorted by capability, then vendor
}

type cardKey struct{ capability, vendor string }

// NonCardFiles are the JSON files a catalog may hold that are not cards, by
// path under the catalog root: the native fetcher's block-rate
// measurement. Every other .json file
// must be a valid card, so a card with misspelled keys fails the load rather
// than vanishing.
var NonCardFiles = []string{"fetch/native_block_rate.json"}

// Load reads every card under fsys. Cards live at <dir>/<vendor>.json, one
// directory deep; the directory groups them (by convention it is the
// capability) and the file is named for the card's vendor. The files in
// NonCardFiles are skipped, and files that are not .json are ignored.
// Anything else that fails to parse fails the load.
func Load(fsys fs.FS) (*Catalog, error) {
	c := &Catalog{cards: map[cardKey]*Card{}}
	origin := map[cardKey]string{}
	var errs []error
	err := fs.WalkDir(fsys, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || path.Ext(p) != ".json" {
			return nil
		}
		if slices.Contains(NonCardFiles, p) {
			return nil
		}
		if strings.Count(p, "/") != 1 {
			errs = append(errs, fmt.Errorf("%s: cards live at <capability>/<vendor>.json, one directory deep", p))
			return nil
		}
		raw, err := fs.ReadFile(fsys, p)
		if err != nil {
			return err
		}
		card, err := ParseCard(raw)
		if err == nil {
			err = checkFloor(&card)
		}
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", p, err))
			return nil
		}
		if want := card.Vendor + ".json"; path.Base(p) != want {
			errs = append(errs, fmt.Errorf("%s: the card's vendor is %s, so its file is %s", p, card.Vendor, want))
			return nil
		}
		k := cardKey{card.Capability, card.Vendor}
		if first, dup := origin[k]; dup {
			errs = append(errs, fmt.Errorf("%s and %s are both the %s/%s card", first, p, k.capability, k.vendor))
			return nil
		}
		origin[k] = p
		c.cards[k] = &card
		c.keys = append(c.keys, k)
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("tool catalog: %w", err)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, fmt.Errorf("tool catalog: %w", err)
	}
	slices.SortFunc(c.keys, func(a, b cardKey) int {
		if a.capability != b.capability {
			return strings.Compare(a.capability, b.capability)
		}
		return strings.Compare(a.vendor, b.vendor)
	})
	return c, nil
}

// LoadDir loads the cards under dir (tools.catalog_dir).
func LoadDir(dir string) (*Catalog, error) {
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("tool catalog: tools.catalog_dir %q is not a directory", dir)
	}
	return Load(os.DirFS(dir))
}

var (
	defaultOnce sync.Once
	defaultCat  *Catalog
	defaultErr  error
)

// SeedCatalog is the catalog of the seed cards compiled into the binary
// (agentcc-gateway/catalog/tools), loaded once. It ignores tools.catalog_dir,
// so nothing prices calls from it: the configured catalog is the one
// LoadForConfig returns.
func SeedCatalog() (*Catalog, error) {
	defaultOnce.Do(func() { defaultCat, defaultErr = Load(seedcards.Cards) })
	return defaultCat, defaultErr
}

// Card returns the card of capability and vendor.
func (c *Catalog) Card(capability, vendor string) (Card, bool) {
	card, ok := c.cards[cardKey{capability, vendor}]
	if !ok {
		return Card{}, false
	}
	return card.clone(), true
}

// Lookup resolves a price_card reference: the card of capability
// and vendor and its SKU. A missing card or SKU is an error wrapping
// ErrDanglingPriceCard.
func (c *Catalog) Lookup(capability, vendor, sku string) (Card, SKU, error) {
	card, ok := c.Card(capability, vendor)
	if !ok {
		return Card{}, SKU{}, fmt.Errorf("%w: no %s/%s card", ErrDanglingPriceCard, capability, vendor)
	}
	s, ok := card.SKU(sku)
	if !ok {
		return Card{}, SKU{}, fmt.Errorf("%w: the %s/%s card has no SKU %s", ErrDanglingPriceCard, capability, vendor, sku)
	}
	return card, s, nil
}

// Cards returns every card, sorted by capability and then vendor.
func (c *Catalog) Cards() []Card {
	out := make([]Card, 0, len(c.keys))
	for _, k := range c.keys {
		out = append(out, c.cards[k].clone())
	}
	return out
}

// VendorCards returns the vendor's cards, sorted by capability.
func (c *Catalog) VendorCards(vendor string) []Card {
	var out []Card
	for _, k := range c.keys {
		if k.vendor == vendor {
			out = append(out, c.cards[k].clone())
		}
	}
	return out
}

// clone copies the card, so a caller cannot change the catalog's copy.
func (c *Card) clone() Card {
	out := *c
	out.SKUs = slices.Clone(c.SKUs)
	for i := range out.SKUs {
		out.SKUs[i].MinUnits = clonePtr(out.SKUs[i].MinUnits)
		out.SKUs[i].MaxUSDPerCall = clonePtr(out.SKUs[i].MaxUSDPerCall)
	}
	out.Regions = slices.Clone(c.Regions)
	return out
}

func clonePtr(p *float64) *float64 {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}
