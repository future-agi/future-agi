package manifest

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"
)

// stringDef is one string $def of connector-manifest/v1: a pattern or an
// enum, optionally with refused values and a length cap. check adds what a
// regular expression cannot say (a real calendar date, a parsable URL).
// TestSchemaParity holds pattern, enum, notEnum and maxLen equal to the
// published schema; check is the Go side being stricter.
type stringDef struct {
	pattern *regexp.Regexp
	enum    []string
	notEnum []string
	maxLen  int
	check   func(string) error
}

func pat(expr string) stringDef { return stringDef{pattern: regexp.MustCompile(expr)} }

func enum(values ...string) stringDef { return stringDef{enum: values} }

// stringDefs are the string $defs, by name. Patterns use only what ECMA-262,
// RE2 and Python agree on.
var stringDefs = map[string]stringDef{
	"appId":       pat(`^[a-z][a-z0-9-]{1,39}$`),
	"providerId":  pat(`^[a-z][a-z0-9-]{1,23}$`),
	"semver":      pat(`^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$`),
	"date":        withCheck(pat(`^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$`), checkDate),
	"httpsUrl":    withMax(withCheck(pat(`^https://[a-z0-9.-]+(:[0-9]{1,5})?([/?][A-Za-z0-9._~:/?@!$&'()*+,;=%-]*)?$`), checkHTTPSURL), 2048),
	"urlTemplate": withMax(withCheck(pat(`^https://([a-z0-9.-]|[{][A-Za-z_][A-Za-z0-9_]{0,31}[}])+(:[0-9]{1,5})?([/?]([A-Za-z0-9._~:/?@!$&'()*+,;=%-]|[{][A-Za-z_][A-Za-z0-9_]{0,31}[}])*)?$`), checkURLTemplate), 2048),
	"hostname":    pat(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`),
	"slug":        pat(`^[a-z][a-z0-9-]{0,31}$`),
	"githubOrg":   pat(`^[A-Za-z0-9][A-Za-z0-9-]{0,38}$`),
	"toolName":    pat(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`),
	"scope":       pat(`^[!-~]{1,256}$`),
	"envName":     pat(`^[A-Z][A-Z0-9_]{0,63}$`),
	"envPrefix":   pat(`^OMEGA_INTEGRATION_[A-Z0-9_]{2,40}$`),
	"varName":     pat(`^[A-Za-z_][A-Za-z0-9_]{0,31}$`),
	"headerName":  withCheck(pat("^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$"), checkHeaderName),
	"queryName":   pat(`^[A-Za-z0-9._~-]{1,64}$`),
	"authorizeParamName": {pattern: regexp.MustCompile(`^[A-Za-z0-9._~-]{1,64}$`),
		notEnum: []string{"client_id", "client_secret", "redirect_uri", "response_type", "state", "scope", "code_challenge", "code_challenge_method", "nonce", "resource"}},
	"sha256Hex":         pat(`^[0-9a-f]{64}$`),
	"toolsHash":         pat(`^sha256:[0-9a-f]{64}$`),
	"ratePeriod":        pat(`^[1-9][0-9]{0,5}(ms|s|m|h|d)$`),
	"leadTime":          withCheck(pat(`^P([0-9]+Y)?([0-9]+M)?([0-9]+W)?([0-9]+D)?$`), checkLeadTime),
	"era":               pat(`^(auto|[0-9]{4}-[0-9]{2}-[0-9]{2})$`),
	"dottedPath":        pat(`^[A-Za-z_][A-Za-z0-9_]{0,63}([.][A-Za-z_][A-Za-z0-9_]{0,63}){0,7}$`),
	"factPath":          pat(`^[A-Za-z][A-Za-z0-9-]{0,63}([.][A-Za-z][A-Za-z0-9-]{0,63}){0,7}$`),
	"packageVersion":    pat(`^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$`),
	"packageIdentifier": pat(`^[A-Za-z0-9@][A-Za-z0-9@._/:-]{0,199}$`),
	"commandName":       pat(`^[A-Za-z0-9._/-]{1,64}$`),
	"operationPath":     pat(`^/[A-Za-z0-9._~:/@!$&'()*+,;=%{}-]{0,255}$`),
	"credentialPrefix":  pat(`^[!-~]{1,31} ?$`),
	"anchoredRegex":     withCheck(pat(`^\^.{1,198}\$$`), checkRegex),
	"registryName":      pat(`^[A-Za-z0-9.-]{1,100}/[A-Za-z0-9._-]{1,99}$`),
	"skillName":         pat(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`),
	"programId":         pat(`^[a-z][a-z0-9_-]{1,47}$`),

	"eeCategory":           enum("productivity", "developer", "communication", "data", "research"),
	"group":                enum("google", "microsoft", "other"),
	"archetype":            enum("workspace-docs", "knowledge-base", "calendar", "mailbox", "code-host", "ci-pipeline", "issue-tracker", "crm", "support-desk", "data-warehouse", "spreadsheet", "cloud-drive", "error-tracking", "product-analytics", "team-messaging"),
	"lifecycleStatus":      enum("active", "deprecated", "sunset"),
	"providerKind":         enum(KindVendorMCP, KindREST, KindPartner, KindVendorLocal),
	"publisher":            enum("vendor", "futureagi", "partner", "community"),
	"vendorStatusLabel":    enum("ga", "preview", "beta", "unknown"),
	"transportKind":        enum("http", "stdio"),
	"transportVariant":     enum("streamable", "sse"),
	"accountMode":          enum("agent", "user"),
	"authKind":             enum(AuthOAuth2, AuthMCPOAuth, AuthAPIKey, AuthBasic, AuthServiceAccount, AuthPartner, AuthNone, AuthLocalSetup),
	"tokenBody":            enum("form", "json"),
	"clientAuth":           enum("body", "basic"),
	"registration":         enum("preregistered", "cimd", "dcr"),
	"credentialIn":         enum("header", "query", "url_variable"),
	"partnerBinding":       enum("header", "url", "mint", "oauth"),
	"toolsSource":          enum(ToolsUpstreamMCP, ToolsOperations, ToolsPartner),
	"posture":              enum(PostureAuto, PostureAsk, PostureDeny),
	"httpMethod":           enum("GET", "POST", "PUT", "PATCH", "DELETE"),
	"pricingModel":         enum("free", "vendor_plan", "vendor_metered", "partner_metered"),
	"tier":                 enum("listed", "verified", "optimized"),
	"conformanceLevel":     enum("L0", "L1", "L2", "L3", "L4", "L5"),
	"specFormat":           enum("openapi-3", "swagger-2", "postman-2.1", "google-discovery"),
	"packageRegistry":      enum("npm", "pypi", "oci", "mcpb"),
	"registryNamespace":    enum("domain", "github"),
	"variableScope":        enum("org", "user"),
	"rateScope":            enum("user", "org", "app"),
	"connectMode":          enum("oauth", "headers"),
	"platformAccount":      enum("byo", "managed"),
	"modality":             enum("text", "image", "audio", "video", "file", "event"),
	"omegaSource":          enum("omega", "personal-gateway-reference", "local-operator-reference", "model-agent-reference", "workflow-automation-reference", "realtime-room-framework", "production-app-reference", "skillpack-reference", "personal-memory-reference", "integration-reference", "runtime-adapter-reference", "methodology-reference", "verification-reference", "external-integration-reference"),
	"include":              enum("include", "exclude"),
	"revocationMethod":     enum("POST", "DELETE"),
	"revocationTokenField": enum("token", "access_token"),
	"verifyMethod":         enum("GET", "POST"),
}

func withCheck(d stringDef, check func(string) error) stringDef { d.check = check; return d }

func withMax(d stringDef, n int) stringDef { d.maxLen = n; return d }

// validate reports why s does not satisfy the def named name.
func (d stringDef) validate(name, s string) error {
	switch {
	case d.maxLen > 0 && len([]rune(s)) > d.maxLen:
		return fmt.Errorf("is longer than %d characters", d.maxLen)
	case d.pattern != nil && !d.pattern.MatchString(s):
		return fmt.Errorf("%q does not match %s", s, name)
	case d.enum != nil && !slices.Contains(d.enum, s):
		return fmt.Errorf("%q is not one of %s", s, strings.Join(d.enum, ", "))
	case slices.Contains(d.notEnum, s):
		return fmt.Errorf("%s is set by the connect flow", s)
	}
	if d.check != nil {
		return d.check(s)
	}
	return nil
}

func checkDate(s string) error {
	if _, err := time.Parse(time.DateOnly, s); err != nil {
		return fmt.Errorf("%s is not a calendar date", s)
	}
	return nil
}

func checkLeadTime(s string) error {
	if s == "P" {
		return fmt.Errorf("lead time %q names no duration", s)
	}
	return nil
}

func checkRegex(s string) error {
	if _, err := regexp.Compile(s); err != nil {
		return fmt.Errorf("pattern does not compile: %v", err)
	}
	return nil
}

// transportHeaders are set by the HTTP or MCP transport; a credential part
// or forwarded header never names one (compared case-insensitively).
var transportHeaders = []string{"host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade", "te",
	"trailer", "keep-alive", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}

func checkHeaderName(s string) error {
	if slices.Contains(transportHeaders, strings.ToLower(s)) {
		return fmt.Errorf("%s is set by the transport", s)
	}
	return nil
}
