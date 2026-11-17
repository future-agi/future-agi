// Package manifest loads and validates the connector catalog
// (connector-manifest/v1, CONNECTORS-PLAN §2.3): one record per app at
// apps/<app-id>/app.yaml, one per way of reaching it at
// apps/<app-id>/providers/<provider-id>.yaml, one per partner platform at
// platforms/<platform-id>.yaml, and a rest provider's generated tools at
// apps/<app-id>/providers/<provider-id>/tools.generated.json.
//
// LoadDir is strict in the tool-price-card way: a key outside the schema, a
// null, a value of the wrong type or outside its bounds is refused at every
// level (only a tool's inputSchema and outputSchema are free JSON Schema
// documents). It then runs L0, the rules every commit must pass, so a
// returned *Catalog is L0-clean. CheckL1 adds the static tool contract,
// SemverDiff the required version bump, and EligibleForEE and EligibleForOSS
// pick what the EE and OSS emitters receive (§2.4).
//
// The `schema` struct tags are the loader's constraints, and
// TestSchemaParity holds them equal to catalog/connectors/schema:
//
//	ref=<def>    the string, each []string element, matches $defs/<def>
//	keys=<def>   each map key matches $defs/<def>
//	len=<a>..<b> string length in characters, or the item or entry count
//	elen=<a>..<b> length of each []string element or map[string]string value
//	range=<a>..<b> the number, or each []int element, is within [a, b]
//	unique       the slice has no duplicate items
//	const=<v>    the string equals v
//	object       a free JSON object (json.RawMessage)
//
// A field whose json tag has omitempty is optional; every other field is
// required.
package manifest

// Record schema identifiers.
const (
	SchemaApp            = "connector-manifest/v1#app"
	SchemaProvider       = "connector-manifest/v1#provider"
	SchemaPlatform       = "connector-manifest/v1#platform"
	SchemaToolsGenerated = "connector-manifest/v1#tools-generated"
)

// Catalog is a loaded, L0-clean catalog. Apps, each app's Providers and
// Platforms are sorted by id.
type Catalog struct {
	Apps      []*App
	Platforms []*Platform
}

// App returns the app with the id, or nil.
func (c *Catalog) App(id string) *App {
	for _, a := range c.Apps {
		if a.ID == id {
			return a
		}
	}
	return nil
}

// Platform returns the platform with the id, or nil.
func (c *Catalog) Platform(id string) *Platform {
	for _, p := range c.Platforms {
		if p.ID == id {
			return p
		}
	}
	return nil
}

// App is one apps/<app-id>/app.yaml record.
type App struct {
	Schema          string       `json:"schema" schema:"const=connector-manifest/v1#app"`
	ID              string       `json:"id" schema:"ref=appId"`
	Version         string       `json:"version" schema:"ref=semver"`
	Name            string       `json:"name" schema:"len=1..60"`
	Description     string       `json:"description" schema:"len=1..100"`
	Vendor          Vendor       `json:"vendor"`
	EECategory      string       `json:"eeCategory" schema:"ref=eeCategory"`
	Group           string       `json:"group" schema:"ref=group"`
	Categories      []string     `json:"categories" schema:"ref=slug,len=1..8,unique"`
	Tags            []string     `json:"tags,omitempty" schema:"ref=slug,len=0..16,unique"`
	DocsURL         string       `json:"docsUrl" schema:"ref=httpsUrl"`
	Icon            *Icon        `json:"icon,omitempty"`
	Archetype       string       `json:"archetype,omitempty" schema:"ref=archetype"`
	DefaultProvider string       `json:"defaultProvider" schema:"ref=providerId"`
	Lifecycle       Lifecycle    `json:"lifecycle"`
	Sources         []Source     `json:"sources" schema:"len=0..64"`
	XOmega          *AppXOmega   `json:"x-omega,omitempty"`
	XGateway        *AppXGateway `json:"x-gateway,omitempty"`

	// Providers are the app's providers, sorted by id (set by LoadDir).
	Providers []*Provider `json:"-"`

	path string // the record's file, for messages
}

// Provider returns the app's provider with the id, or nil.
func (a *App) Provider(id string) *Provider {
	for _, p := range a.Providers {
		if p.ID == id {
			return p
		}
	}
	return nil
}

// Vendor is the curated vendor identity the registry importer matches on.
type Vendor struct {
	Name       string   `json:"name" schema:"len=1..60"`
	Domains    []string `json:"domains" schema:"ref=hostname,len=1..16,unique"`
	GitHubOrgs []string `json:"githubOrgs,omitempty" schema:"ref=githubOrg,len=0..16,unique"`
}

// Icon holds an app's icon URLs.
type Icon struct {
	Light string `json:"light" schema:"ref=httpsUrl"`
	Dark  string `json:"dark,omitempty" schema:"ref=httpsUrl"`
}

// Lifecycle is an app's lifecycle; ReplacedBy names another app.
type Lifecycle struct {
	Status     string `json:"status" schema:"ref=lifecycleStatus"`
	SunsetAt   string `json:"sunsetAt,omitempty" schema:"ref=date"`
	ReplacedBy string `json:"replacedBy,omitempty" schema:"ref=appId"`
}

// Source records where a fact was read, and when.
type Source struct {
	Fact      string `json:"fact" schema:"ref=factPath"`
	URL       string `json:"url" schema:"ref=httpsUrl"`
	FetchedAt string `json:"fetchedAt" schema:"ref=date"`
}

// AppXOmega holds the Omega-only app keys. EEID, EnvPrefix and the EE and
// OSS include flags keep EE's existing app ids and env variables
// (omega-coordination.md, the 05:40 agreement).
type AppXOmega struct {
	EEID       string   `json:"eeId,omitempty" schema:"ref=appId"`
	EnvPrefix  string   `json:"envPrefix,omitempty" schema:"ref=envPrefix"`
	EE         string   `json:"ee,omitempty" schema:"ref=include"`
	OSS        string   `json:"oss,omitempty" schema:"ref=include"`
	Modalities []string `json:"modalities,omitempty" schema:"ref=modality,len=0..6,unique"`
	Skills     []string `json:"skills,omitempty" schema:"ref=skillName,len=0..32,unique"`
	Sources    []string `json:"sources,omitempty" schema:"ref=omegaSource,len=0..14,unique"`
}

// AppXGateway holds gateway-only app keys; v1 defines none.
type AppXGateway struct{}

// Platform is one platforms/<platform-id>.yaml record: a partner platform
// whose providers hold the SaaS tokens and run the tools.
type Platform struct {
	Schema         string          `json:"schema" schema:"const=connector-manifest/v1#platform"`
	ID             string          `json:"id" schema:"ref=appId"`
	Version        string          `json:"version" schema:"ref=semver"`
	Name           string          `json:"name" schema:"len=1..60"`
	Description    string          `json:"description" schema:"len=1..100"`
	Connect        PlatformConnect `json:"connect"`
	Account        string          `json:"account" schema:"ref=platformAccount"`
	ToolMode       string          `json:"toolMode" schema:"const=fixed"`
	MetaTools      []string        `json:"metaTools" schema:"ref=toolName,len=0..16,unique"`
	SelfHostable   bool            `json:"selfHostable"`
	TermsURL       string          `json:"termsUrl" schema:"ref=httpsUrl"`
	TermsCheckedAt string          `json:"termsCheckedAt" schema:"ref=date"`
	Sources        []Source        `json:"sources" schema:"len=0..64"`

	path string
}

// PlatformConnect says how the gateway connects to a platform.
type PlatformConnect struct {
	Modes      []string `json:"modes" schema:"ref=connectMode,len=1..2,unique"`
	URLPattern string   `json:"urlPattern" schema:"ref=urlTemplate"`
}
