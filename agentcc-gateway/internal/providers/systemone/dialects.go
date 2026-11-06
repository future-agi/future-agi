package systemone

import (
	"errors"
	"net/url"
	"strings"
)

// Dialect is one vendor's take on the System One wire format. The body is the
// same everywhere; what differs is where it is posted and how the key is sent.
type Dialect struct {
	// Name identifies the dialect, and is the model prefix an internal caller
	// uses to pick it ("typesafe/jev-1.13.0").
	Name string
	// Vendor names the upstream in errors.
	Vendor string
	// DefaultBase is used when the provider states no base_url. Empty means a
	// base_url is required.
	DefaultBase string
	// Leaf is the endpoint path, joined after the base URL and the provider's
	// version prefix ("/v1" unless api_path_prefix says otherwise).
	Leaf string
	// AuthHeader and AuthScheme carry the key: "Authorization: Bearer <key>".
	AuthHeader, AuthScheme string
	// KeyRequired refuses a call locally when the provider has no key.
	KeyRequired bool

	// hostSuffix recognises the vendor from a base_url.
	hostSuffix string
}

var (
	// TypeSafe is TypeSafe AI's Jev models, which defined the format.
	TypeSafe = Dialect{
		Name: "typesafe", Vendor: "TypeSafe",
		DefaultBase: "https://api.typesafe.ai", Leaf: "/v1/systemone",
		AuthHeader: "Authorization", AuthScheme: "Bearer ", KeyRequired: true,
		hostSuffix: "typesafe.ai",
	}

	// Perplexity is Perplexity's Decider. It rejects x-api-key: Bearer only.
	Perplexity = Dialect{
		Name: "perplexity", Vendor: "Perplexity",
		DefaultBase: "https://api.perplexity.ai", Leaf: "/v1/decisions",
		AuthHeader: "Authorization", AuthScheme: "Bearer ", KeyRequired: true,
		hostSuffix: "perplexity.ai",
	}

	// Generic is any other System One endpoint: Microsoft Foundry (base_url
	// "<resource>/providers/microsoft"), a self-hosted Strands or vLLM server.
	// It has no default upstream. An explicitly configured endpoint may run
	// without a key, including one owned by an organization.
	Generic = Dialect{
		Name: "generic", Vendor: "System One endpoint",
		Leaf:       "/v1/systemone",
		AuthHeader: "Authorization", AuthScheme: "Bearer ",
	}

	vendorDialects = []Dialect{TypeSafe, Perplexity}
)

// ErrBaseURLRequired is returned for a provider that names no known vendor
// and states no base_url: there is nowhere to send its key.
var ErrBaseURLRequired = errors.New("systemone: a custom System One endpoint needs a base_url")

// DialectByName returns a vendor dialect by its name.
func DialectByName(name string) (Dialect, bool) {
	for _, d := range vendorDialects {
		if strings.EqualFold(d.Name, name) {
			return d, true
		}
	}
	return Dialect{}, false
}

// DialectFor picks the dialect for a provider from its base_url's host, then
// from its ID ("perplexity-decider", "typesafe"); any other provider with a
// base_url is Generic.
func DialectFor(providerID, baseURL string) (Dialect, error) {
	if d, ok := vendorByHost(baseURL); ok {
		return d, nil
	}
	if d, ok := vendorByID(providerID); ok {
		return d, nil
	}
	if strings.TrimSpace(baseURL) != "" {
		return Generic, nil
	}
	return Dialect{}, ErrBaseURLRequired
}

// IsVendor reports whether a provider's ID or base_url names a System One
// vendor. A provider that does but is saved with another api_format is
// misconfigured, not a provider of that other format.
func IsVendor(providerID, baseURL string) bool {
	_, byHost := vendorByHost(baseURL)
	_, byID := vendorByID(providerID)
	return byHost || byID
}

func vendorByHost(baseURL string) (Dialect, bool) {
	u, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil {
		return Dialect{}, false
	}
	host := strings.TrimSuffix(strings.ToLower(u.Hostname()), ".")
	for _, d := range vendorDialects {
		if host == d.hostSuffix || strings.HasSuffix(host, "."+d.hostSuffix) {
			return d, true
		}
	}
	return Dialect{}, false
}

// vendorByID reads the vendor from a provider ID. Org providers are built
// under "<id>_org_<org>", so only the part before that suffix counts.
func vendorByID(providerID string) (Dialect, bool) {
	name, _, _ := strings.Cut(strings.ToLower(providerID), "_org_")
	for _, d := range vendorDialects {
		if strings.HasPrefix(name, d.Name) {
			return d, true
		}
	}
	return Dialect{}, false
}
