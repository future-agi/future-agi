package manifest

// Provider kinds: K1 vendor_mcp, K2 rest, K3 partner, K4 vendor_local.
const (
	KindVendorMCP   = "vendor_mcp"
	KindREST        = "rest"
	KindPartner     = "partner"
	KindVendorLocal = "vendor_local"
)

// Auth kinds.
const (
	AuthOAuth2         = "oauth2"
	AuthMCPOAuth       = "mcp_oauth"
	AuthAPIKey         = "api_key"
	AuthBasic          = "basic"
	AuthServiceAccount = "service_account"
	AuthPartner        = "partner"
	AuthNone           = "none"
	AuthLocalSetup     = "local_setup"
)

// Provider is one apps/<app-id>/providers/<provider-id>.yaml record: one way
// to reach the app.
type Provider struct {
	Schema       string            `json:"schema" schema:"const=connector-manifest/v1#provider"`
	App          string            `json:"app" schema:"ref=appId"`
	ID           string            `json:"id" schema:"ref=providerId"`
	Version      string            `json:"version" schema:"ref=semver"`
	Kind         string            `json:"kind" schema:"ref=providerKind"`
	Publisher    string            `json:"publisher" schema:"ref=publisher"`
	VendorStatus VendorStatus      `json:"vendorStatus"`
	Transport    Transport         `json:"transport"`
	Hosted       bool              `json:"hosted"`
	Trusted      bool              `json:"trusted"`
	AccountModes []string          `json:"accountModes" schema:"ref=accountMode,len=1..2,unique"`
	Auth         Auth              `json:"auth"`
	Tools        Tools             `json:"tools"`
	RateLimits   []RateLimit       `json:"rateLimits,omitempty" schema:"len=0..16"`
	Retry        *Retry            `json:"retry,omitempty"`
	Pricing      Pricing           `json:"pricing"`
	Upstream     *Upstream         `json:"upstream,omitempty"`
	Quality      Quality           `json:"quality"`
	Lifecycle    ProviderLifecycle `json:"lifecycle"`
	Sources      []Source          `json:"sources" schema:"len=0..64"`
	XOmega       *ProviderXOmega   `json:"x-omega,omitempty"`
	XGateway     *ProviderXGateway `json:"x-gateway,omitempty"`

	// GeneratedTools are the tools of tools.generated.json, when
	// Tools.Generated names it (set by LoadDir).
	GeneratedTools []Tool `json:"-"`

	path, generatedPath string // the record's files, for messages
	app                 *App   // the app LoadDir attached it to, for the semver diff
}

// AllTools returns the provider's curated and generated tools.
func (p *Provider) AllTools() []Tool {
	out := make([]Tool, 0, len(p.Tools.Items)+len(p.GeneratedTools))
	out = append(out, p.Tools.Items...)
	return append(out, p.GeneratedTools...)
}

// VendorStatus is how the vendor labels the surface this provider uses.
type VendorStatus struct {
	Label  string `json:"label" schema:"ref=vendorStatusLabel"`
	Source string `json:"source,omitempty" schema:"ref=httpsUrl"`
}

// Transport says how the provider's MCP server is reached.
type Transport struct {
	Kind         string                 `json:"kind" schema:"ref=transportKind"`
	URL          string                 `json:"url,omitempty" schema:"ref=urlTemplate"`
	URLVariables map[string]URLVariable `json:"urlVariables,omitempty" schema:"keys=varName,len=0..8"`
	Variant      string                 `json:"variant,omitempty" schema:"ref=transportVariant"`
	Era          string                 `json:"era,omitempty" schema:"ref=era"`
	Command      string                 `json:"command,omitempty" schema:"ref=commandName"`
	Args         []string               `json:"args,omitempty" schema:"len=0..32,elen=1..256"`
	Env          map[string]string      `json:"env,omitempty" schema:"keys=envName,len=0..16,elen=1..200"`
	Package      *Package               `json:"package,omitempty"`
}

// URLVariable is one {name} in a URL template, filled per org or per user.
type URLVariable struct {
	Description string `json:"description" schema:"len=1..200"`
	Pattern     string `json:"pattern" schema:"ref=anchoredRegex"`
	Default     string `json:"default,omitempty" schema:"len=1..253"`
	Scope       string `json:"scope" schema:"ref=variableScope"`
}

// Package is a stdio server's published package.
type Package struct {
	RegistryType string `json:"registryType" schema:"ref=packageRegistry"`
	Identifier   string `json:"identifier" schema:"ref=packageIdentifier"`
	Version      string `json:"version" schema:"ref=packageVersion"`
	FileSHA256   string `json:"fileSha256,omitempty" schema:"ref=sha256Hex"`
}

// Auth says how a caller authenticates to the provider.
type Auth struct {
	Kind            string           `json:"kind" schema:"ref=authKind"`
	OAuth2          *OAuth2          `json:"oauth2,omitempty"`
	CredentialParts []CredentialPart `json:"credentialParts,omitempty" schema:"len=1..8"`
	ClientApproval  *ClientApproval  `json:"clientApproval,omitempty"`
	Partner         *PartnerAuth     `json:"partner,omitempty"`
	Verify          *Verify          `json:"verify,omitempty"`
}

// OAuth2 is a declared OAuth 2 spec: EE's IntegrationOAuthSpec field for
// field, plus the extras.
type OAuth2 struct {
	// ClientSources is declared only at auth.oauth2.clientSources.
	ClientSources        []string               `json:"clientSources,omitempty" schema:"len=0..2,elen=1..32,unique"`
	AuthorizeURL         string                 `json:"authorizeUrl" schema:"ref=urlTemplate"`
	TokenURL             string                 `json:"tokenUrl" schema:"ref=urlTemplate"`
	Scopes               []string               `json:"scopes" schema:"ref=scope,len=0..64,unique"`
	PKCE                 bool                   `json:"pkce"`
	TokenBody            string                 `json:"tokenBody" schema:"ref=tokenBody"`
	ClientAuth           string                 `json:"clientAuth" schema:"ref=clientAuth"`
	ExtraAuthorizeParams map[string]string      `json:"extraAuthorizeParams,omitempty" schema:"keys=authorizeParamName,len=0..16,elen=1..200"`
	ResourceIndicator    bool                   `json:"resourceIndicator,omitempty"`
	URLVariables         map[string]URLVariable `json:"urlVariables,omitempty" schema:"keys=varName,len=0..8"`
	Refresh              *Refresh               `json:"refresh,omitempty"`
	Revocation           *Revocation            `json:"revocation,omitempty"`
	Registration         []string               `json:"registration,omitempty" schema:"ref=registration,len=1..3,unique"`
	ClientGroup          string                 `json:"clientGroup,omitempty" schema:"ref=appId"`
	Review               *OAuthReview           `json:"review,omitempty"`
	TokenResponse        *TokenResponse         `json:"tokenResponse,omitempty"`
}

// Refresh says whether refresh tokens are issued and rotated.
type Refresh struct {
	Supported bool `json:"supported"`
	Rotates   bool `json:"rotates"`
}

// Revocation is the vendor's token revocation endpoint (EE's old-plane
// shape).
type Revocation struct {
	URL                    string            `json:"url" schema:"ref=urlTemplate"`
	Method                 string            `json:"method" schema:"ref=revocationMethod"`
	TokenBody              string            `json:"tokenBody" schema:"ref=tokenBody"`
	TokenField             string            `json:"tokenField" schema:"ref=revocationTokenField"`
	ClientAuth             string            `json:"clientAuth" schema:"ref=clientAuth"`
	Headers                map[string]string `json:"headers,omitempty" schema:"keys=headerName,len=0..8,elen=1..200"`
	AlreadyRevokedStatuses []int             `json:"alreadyRevokedStatuses,omitempty" schema:"len=0..8,unique,range=200..599"`
}

// OAuthReview is the vendor's app review a FutureAGI client needs, and its
// lead time.
type OAuthReview struct {
	Program  string `json:"program" schema:"ref=programId"`
	LeadTime string `json:"leadTime" schema:"ref=leadTime"`
	Source   string `json:"source" schema:"ref=httpsUrl"`
}

// TokenResponse locates tokens a vendor returns away from the top level
// (Slack's authed_user, for one).
type TokenResponse struct {
	AccessTokenPath  string `json:"accessTokenPath" schema:"ref=dottedPath"`
	RefreshTokenPath string `json:"refreshTokenPath,omitempty" schema:"ref=dottedPath"`
	ExpiresInPath    string `json:"expiresInPath,omitempty" schema:"ref=dottedPath"`
}

// CredentialPart is one part of a multi-part credential and where it goes.
// A secret part never carries a value.
type CredentialPart struct {
	In      string `json:"in" schema:"ref=credentialIn"`
	Name    string `json:"name" schema:"len=1..64"`
	Secret  bool   `json:"secret"`
	Value   string `json:"value,omitempty" schema:"len=1..200"`
	Prefix  string `json:"prefix,omitempty" schema:"ref=credentialPrefix"`
	Label   string `json:"label,omitempty" schema:"len=1..60"`
	Help    string `json:"help,omitempty" schema:"len=1..300"`
	HelpURL string `json:"helpUrl,omitempty" schema:"ref=httpsUrl"`
}

// ClientApproval records a vendor that admits only clients it has approved.
// ApprovedAt is the date FutureAGI's client was approved.
type ClientApproval struct {
	Required   bool   `json:"required"`
	Program    string `json:"program" schema:"len=1..100"`
	URL        string `json:"url" schema:"ref=httpsUrl"`
	Source     string `json:"source" schema:"ref=httpsUrl"`
	ApprovedAt string `json:"approvedAt,omitempty" schema:"ref=date"`
}

// Pending reports whether the vendor requires client approval that FutureAGI
// does not have yet.
func (c *ClientApproval) Pending() bool {
	return c != nil && c.Required && c.ApprovedAt == ""
}

// PartnerAuth binds a partner provider to its platform toolkit.
type PartnerAuth struct {
	Platform string `json:"platform" schema:"ref=appId"`
	Toolkit  string `json:"toolkit" schema:"len=1..100"`
	Version  string `json:"version" schema:"len=1..64"`
	Binding  string `json:"binding" schema:"ref=partnerBinding"`
	ToolMode string `json:"toolMode" schema:"const=fixed"`
}

// Verify is the credential check at connect time: a tool or an HTTP call.
type Verify struct {
	Tool string      `json:"tool,omitempty" schema:"ref=toolName"`
	HTTP *VerifyHTTP `json:"http,omitempty"`
}

// VerifyHTTP is an HTTP credential check.
type VerifyHTTP struct {
	Method string `json:"method" schema:"ref=verifyMethod"`
	URL    string `json:"url" schema:"ref=urlTemplate"`
}

// RateLimit is one documented vendor rate limit.
type RateLimit struct {
	Scope    string `json:"scope" schema:"ref=rateScope"`
	Requests int    `json:"requests" schema:"range=1..100000000"`
	Per      string `json:"per" schema:"ref=ratePeriod"`
	Source   string `json:"source" schema:"ref=httpsUrl"`
}

// Retry says which statuses are retried.
type Retry struct {
	On              []int `json:"on" schema:"len=1..8,unique,range=400..599"`
	HonorRetryAfter bool  `json:"honorRetryAfter"`
}

// Pricing is the pricing model only, never a number.
type Pricing struct {
	Model  string `json:"model" schema:"ref=pricingModel"`
	Notes  string `json:"notes,omitempty" schema:"len=1..200"`
	Source string `json:"source,omitempty" schema:"ref=httpsUrl"`
}

// Upstream names the official registry entry and the REST spec a provider
// is built from.
type Upstream struct {
	Registry *RegistryRef `json:"registry,omitempty"`
	Spec     *Spec        `json:"spec,omitempty"`
}

// RegistryRef is an official MCP Registry entry.
type RegistryRef struct {
	Name      string `json:"name" schema:"ref=registryName"`
	Version   string `json:"version" schema:"ref=packageVersion"`
	Namespace string `json:"namespace" schema:"ref=registryNamespace"`
}

// Spec is the vendor's API description a rest provider is generated from,
// pinned by sha256.
type Spec struct {
	URL     string `json:"url" schema:"ref=httpsUrl"`
	Format  string `json:"format" schema:"ref=specFormat"`
	SHA256  string `json:"sha256" schema:"ref=sha256Hex"`
	Licence string `json:"licence" schema:"len=1..100"`
}

// Quality is the provider's tier and the proof behind it.
type Quality struct {
	Tier        string         `json:"tier" schema:"ref=tier"`
	Conformance *Conformance   `json:"conformance,omitempty"`
	LiveSmoke   *LiveSmoke     `json:"liveSmoke,omitempty"`
	Evals       *Evals         `json:"evals,omitempty"`
	Review      *QualityReview `json:"review,omitempty"`
	Owner       string         `json:"owner,omitempty" schema:"ref=slug"`
}

// Conformance is the last conformance run.
type Conformance struct {
	Level          string `json:"level" schema:"ref=conformanceLevel"`
	Harness        string `json:"harness" schema:"ref=semver"`
	FixturesSHA256 string `json:"fixturesSha256" schema:"ref=sha256Hex"`
	RunAt          string `json:"runAt" schema:"ref=date"`
}

// LiveSmoke is the last passing live smoke test, always on a test account.
type LiveSmoke struct {
	LastPass string `json:"lastPass" schema:"ref=date"`
	Account  string `json:"account" schema:"const=test"`
}

// Evals is the last agent eval run.
type Evals struct {
	Suite    string   `json:"suite" schema:"len=1..100"`
	PassRate float64  `json:"passRate" schema:"range=0..1"`
	Models   []string `json:"models" schema:"len=1..8,unique,elen=1..100"`
	RunAt    string   `json:"runAt" schema:"ref=date"`
}

// QualityReview is the maintainer review.
type QualityReview struct {
	By    string `json:"by" schema:"len=1..100"`
	At    string `json:"at" schema:"ref=date"`
	Notes string `json:"notes,omitempty" schema:"len=1..500"`
}

// ProviderLifecycle is a provider's lifecycle; ReplacedBy names another
// provider of the same app.
type ProviderLifecycle struct {
	Status     string `json:"status" schema:"ref=lifecycleStatus"`
	SunsetAt   string `json:"sunsetAt,omitempty" schema:"ref=date"`
	ReplacedBy string `json:"replacedBy,omitempty" schema:"ref=providerId"`
}

// ProviderXOmega holds the Omega-only provider keys.
type ProviderXOmega struct {
	EE           string        `json:"ee,omitempty" schema:"ref=include"`
	OSS          string        `json:"oss,omitempty" schema:"ref=include"`
	OSSID        string        `json:"ossId,omitempty" schema:"ref=appId"`
	PublicClient *PublicClient `json:"publicClient,omitempty"`
}

// PublicClient says whether a person can sign in to the provider the way
// OSS Omega's `omega mcp login` does: with a client id registered with the
// vendor beforehand, PKCE and no client secret (the OSS
// rule). Each evidence item is a page saved on a date with its sha256 and
// the sentence on it that bears the fact out. Not stated means not proven.
type PublicClient struct {
	Supported bool                   `json:"supported"`
	Evidence  []PublicClientEvidence `json:"evidence" schema:"len=1..4"`
	Notes     string                 `json:"notes,omitempty" schema:"len=1..500"`
}

// PublicClientEvidence is one saved page behind a PublicClient fact.
type PublicClientEvidence struct {
	URL       string `json:"url" schema:"ref=httpsUrl"`
	FetchedAt string `json:"fetchedAt" schema:"ref=date"`
	SHA256    string `json:"sha256" schema:"ref=sha256Hex"`
	Quote     string `json:"quote" schema:"len=1..300"`
}

// ConnectionEnablementDeclaration is the catalog owner's connection switch.
// This minimal reader projection matches the regenerated contract; absence does not enable K2.
// schemaVersion is pinned to 1 by the range constraint, which the shape check,
// the parity test and the JSON Schema all express alike.
type ConnectionEnablementDeclaration struct {
	SchemaVersion int      `json:"schemaVersion" schema:"range=1..1"`
	Enabled       bool     `json:"enabled"`
	Operations    []string `json:"operations" schema:"len=0..3,elen=1..32,unique"`
}

// ProviderXGateway holds the gateway-only provider keys.
type ProviderXGateway struct {
	ConnectionEnablement *ConnectionEnablementDeclaration `json:"connectionEnablement,omitempty"`
	TimeoutMs            int                              `json:"timeoutMs,omitempty" schema:"range=1000..300000"`
	ForwardHeaders       []string                         `json:"forwardHeaders,omitempty" schema:"ref=headerName,len=0..16,unique"`
}
