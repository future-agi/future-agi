// Package capability is the gateway's one capability core: every REST
// capability handler and every MCP
// built-in calls a vendor adapter only through Do, which picks the vendor
// (BYOK first, managed only when mcp.managed_tools admits it, the card's
// terms enforced), falls back by the capability's class, prices units from
// the vendor's tool price card, holds and settles managed calls on the
// managed-credit store, and records attempts, usage and terms-aware
// retention for the request log.
package capability

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Billing modes of a capability usage block.
const (
	BillingManaged = "managed"
	BillingBYOK    = "byok"
	BillingNative  = "native"
)

// Cost sources of a capability usage block.
const (
	CostVendorReported = "vendor_reported"
	CostRateCard       = "rate_card"
	CostNative         = "native"
	CostCacheHit       = "cache_hit"
)

// Request is one normalized capability call.
type Request struct {
	// Org is the caller's effective org config; nil reads as no org config.
	Org *tenant.OrgConfig
	// Providers narrows the vendors: empty or ["auto"] is the org's order or
	// the cards' default; otherwise the named vendors, in order, which pins
	// them. It never widens the org's list.
	Providers []string
	// Tier picks the SKU (a depth tier); "" is the card's default SKU.
	Tier string
	// Units is how many of the SKU's units the call asks for, for the
	// worst-case hold and for rate-card pricing when the vendor reports no
	// units; 0 reads as the SKU's min_units, else 1. A counted unit (request,
	// result, page, image, ...) is rounded up to a whole count (callUnits).
	Units float64
	// Body is the normalized request, sent to the adapter as it is.
	Body json.RawMessage
	// UsesAIFeatures is set when the call uses a vendor's AI features, which
	// a trains_on_input=ai_features_only vendor trains on.
	UsesAIFeatures bool
	// MCP is set when the call comes from an MCP built-in: the result
	// then carries the billing meta the MCP layer copies.
	MCP bool
	// UserToken is set when the caller is an OAuth principal (an MCP user
	// token): a managed candidate then also needs
	// mcp.managed_tools.allow_user_tokens.
	UserToken bool
}

// Unit is a count of one SKU.
type Unit struct {
	SKU   string  `json:"sku"`
	Count float64 `json:"count"`
}

// Usage is a capability response's usage block (capability-usage-v1).
// Upstream cost and charge are in USD.
type Usage struct {
	Provider        string  `json:"provider"`
	Units           []Unit  `json:"units"`
	UpstreamCostUSD float64 `json:"upstream_cost_usd"`
	ChargedUSD      float64 `json:"charged_usd"`
	BillingMode     string  `json:"billing_mode"`
	CostSource      string  `json:"cost_source"`
	Estimated       bool    `json:"estimated"`
}

// Result is a capability call's answer.
type Result struct {
	// Provider is the vendor that served.
	Provider string
	// Body is the vendor's response as the adapter normalized it.
	Body  json.RawMessage
	Usage Usage
	// Attempts are the call's attempts, in order.
	Attempts []models.Attempt
	// Meta is the MCP _meta object: io.futureagi/billing when the request
	// came from an MCP built-in (Request.MCP), else nil.
	Meta map[string]any
}

// Credential is what a vendor call authenticates with.
type Credential struct {
	Type    models.CredentialType
	APIKey  string
	BaseURL string
}

// VendorCall is one adapter call.
type VendorCall struct {
	Capability string
	Vendor     string
	Card       *Card
	SKU        SKU
	Credential Credential
	Request    Request
}

// VendorResponse is what an adapter got back. Units and CostUSD are what the
// vendor reported, when it did; the core prices from the rate card otherwise.
type VendorResponse struct {
	Body    json.RawMessage
	Units   []Unit
	CostUSD *float64
}

// Adapter calls one vendor for one capability.
type Adapter interface {
	Call(ctx context.Context, call VendorCall) (VendorResponse, error)
}

// AdapterFunc adapts a function to Adapter.
type AdapterFunc func(ctx context.Context, call VendorCall) (VendorResponse, error)

// Call calls f.
func (f AdapterFunc) Call(ctx context.Context, call VendorCall) (VendorResponse, error) {
	return f(ctx, call)
}

// VendorError is a vendor's refusal or failure. Status is the vendor's HTTP
// status (0 when the vendor never answered). Billed is set when the vendor
// charges this failure whatever the card's free_on_failure says.
// SessionCreated is set when an upstream session or job id already existed,
// which ends fallback for a create-time-only capability.
type VendorError struct {
	Status         int
	Message        string
	Billed         bool
	SessionCreated bool
}

func (e *VendorError) Error() string {
	if e.Status > 0 {
		return fmt.Sprintf("vendor returned %d: %s", e.Status, e.Message)
	}
	return "vendor call failed: " + e.Message
}
