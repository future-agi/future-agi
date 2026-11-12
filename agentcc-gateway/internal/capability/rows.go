package capability

import (
	"math"

	"github.com/futureagi/agentcc-gateway/internal/capabilities"
)

// ToolCells describe every vendor of every published tool capability this
// core serves, for /-/capabilities and /v1/capabilities: served by
// FutureAGI's managed credential where the card's terms allow a managed
// call (supported, managed), else only with the org's own key
// (provider_dependent, byok); managed_allowed when that call is priced; the
// four-state resale value; and the published managed price, markup included.
func (c *Core) ToolCells() []capabilities.ToolCell {
	var out []capabilities.ToolCell
	for _, capability := range c.reg.Capabilities() {
		if !capabilities.IsToolEndpoint(capability) {
			continue
		}
		for _, vendor := range c.reg.Vendors(capability) {
			card := cardOf(c.cards, capability, vendor)
			if card == nil {
				continue
			}
			out = append(out, capabilities.ToolCell{Endpoint: capability, Vendor: vendor, Cell: c.toolCell(card)})
		}
	}
	return out
}

func (c *Core) toolCell(card *Card) capabilities.Cell {
	cell := capabilities.Cell{Status: capabilities.StatusProviderDependent, CredentialType: capabilities.CredentialBYOK,
		Source: capabilities.SourceConfig, ManagedResaleTerms: resaleTerms(card)}
	_, held := c.creds.Managed(card.Vendor)
	managed := held && card.managedRefusal() == ""
	if managed {
		cell.Status, cell.CredentialType = capabilities.StatusSupported, capabilities.CredentialManaged
	}
	sku, ok := card.SKUFor("")
	if managed && ok && (sku.USDPerUnit > 0 || !sku.CostFromResultMeta) {
		cell.ManagedAllowed = true
		cell.Price = &capabilities.Price{SKU: sku.SKU, USDPerUnitBilled: billedPrice(sku.USDPerUnit, c.opts.MarkupBps),
			MarkupPercent: float64(c.opts.MarkupBps) / 100}
	}
	return cell
}

// resaleTerms is the card's managed_resale_terms value.
func resaleTerms(card *Card) string {
	switch {
	case card.ResaleStatus == ResaleNative:
		return capabilities.ResaleNative
	case card.ResaleStatus == ResaleBYOKOnly:
		return capabilities.ResaleBYOKOnly
	case card.ResaleTermsRef != "":
		return capabilities.ResaleCleared
	}
	return capabilities.ResaleUnconfirmed
}

// billedPrice is a unit price with the markup, to the picodollar.
func billedPrice(usd float64, bps int) float64 {
	return math.Round(usd*float64(bpsDenom+bps)/bpsDenom*picoPerUSD) / picoPerUSD
}
