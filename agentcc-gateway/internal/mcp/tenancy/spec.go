// Package tenancy is per-org MCP tenancy: one pool of
// upstream clients per org, built from the org's pushed mcp config, plus the
// operator's static YAML pool, which serves only principals of
// mcp.static.org_id and principals with no org. Clients are built only
// through the MCP server's Deps.ClientFactory (read when a pool builds, never
// captured), rebuilt only when their upstream fingerprint changes, retried
// with backoff when they fail, and a config the pool cannot load keeps the
// last-known-good pool.
package tenancy

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Server sources (mcp.PoolServer.Source).
const (
	SourceOrg     = "org"
	SourceManaged = "managed"
	SourceStatic  = "static"
)

// spec is one upstream server as a pool builds it.
type spec struct {
	id     string
	source string
	// admit is the binding this spec's clients are built for, as an admission
	// request (see settingsRequest); inventory is the offline tool list the
	// policy gave when the entry was constructed. Neither is a verdict: every
	// credential lookup and every send asks the policy again.
	admit     mcp.ClientAdmissionRequest
	inventory []mcp.Tool
	// builtin is the Deps.Builtins id that serves a kind: builtin managed
	// server; "" for a server reached through Deps.ClientFactory.
	builtin string
	// builtinIDs are the ids tried in Deps.Builtins, in order.
	builtinIDs []string
	perUser    bool
	// sharedOAuth: the client pins the org's signed-in OAuth grant from
	// Deps.SharedCredentials. signedIn means the pushed config names
	// that account as active, so the server can connect.
	sharedOAuth bool
	signedIn    bool
	client      mcp.ClientConfig

	era                string
	transport          string
	timeoutMs          int
	cacheTTLSeconds    int
	forwardHeaders     []string
	propagateDepth     bool
	trustedAnnotations bool
	credentialState    string
	credentialFP       string
	credentialGrant    string
	credentialRevision int64
	credentialRequired bool
	credentialExpiry   string

	// fp is the upstream fingerprint: a change rebuilds the client; a change
	// to anything else only replaces the spec.
	fp string
}

// serverIDPattern is the org config's server id rule.
var serverIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,47}$`)

// orgSpecs reads the servers of an org config: the valid ones, and a load
// error for each server the pool refuses. A disabled org or server has none.
// cat is the connector catalog the servers added from it are placed by (nil:
// none loaded, and such a server's shared credential is refused).
func orgSpecs(cfg *tenant.OrgConfig, cat credtemplate.Catalog) (map[string]spec, map[string]error) {
	return orgSpecsForRelease(cfg, cat, false)
}

func orgSpecsForRelease(cfg *tenant.OrgConfig, cat credtemplate.Catalog, catalogEnabled bool) (map[string]spec, map[string]error) {
	specs, errs := map[string]spec{}, map[string]error{}
	if cfg == nil || cfg.MCP == nil || !cfg.MCP.Enabled {
		return specs, errs
	}
	for id, raw := range cfg.MCP.Servers {
		if raw == nil {
			continue
		}
		s, _ := cfg.MCPServer(id)
		if !s.Enabled || (!catalogEnabled && s.CatalogRef != "") {
			continue
		}
		sp, err := orgSpec(id, s, cat)
		if err != nil {
			errs[id] = err
			continue
		}
		specs[id] = sp
	}
	return specs, errs
}

// orgSpec validates one org server: an operator-only stdio upstream, a
// missing or non-http(s) URL, an unknown era, a refused forwarded header, a
// reserved id or a credential the gateway cannot place makes the whole
// config unloadable (last-known-good).
func orgSpec(id string, s tenant.MCPServerSettings, cat credtemplate.Catalog) (spec, error) {
	if !serverIDPattern.MatchString(id) {
		return spec{}, fmt.Errorf("server id %q is not ^[a-z0-9][a-z0-9_-]{0,47}$", id)
	}
	switch s.Transport {
	case "", "http", "sse":
	case "stdio":
		return spec{}, fmt.Errorf("server %q: stdio servers are defined only in the operator's YAML", id)
	default:
		return spec{}, fmt.Errorf("server %q: transport %q is not http or sse", id, s.Transport)
	}
	if u, err := url.Parse(s.URL); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return spec{}, fmt.Errorf("server %q: url is not an http(s) URL with a host", id)
	}
	if s.Era != "auto" && !slices.Contains(config.MCPProtocolVersions(), s.Era) {
		return spec{}, fmt.Errorf("server %q: era %q is not auto or a served revision", id, s.Era)
	}
	for _, h := range s.ForwardHeaders {
		if config.ForwardHeaderRefused(h) {
			return spec{}, fmt.Errorf("server %q: forward_headers names %q, which is never forwarded", id, h)
		}
	}
	transport := s.Transport
	if transport == "" {
		transport = "http"
	}
	sharedOAuth, err := sharedOAuthOf(s)
	if err != nil {
		return spec{}, fmt.Errorf("server %q: %w", id, err)
	}
	sp := spec{
		id:                 id,
		source:             SourceOrg,
		admit:              settingsRequest(s),
		perUser:            s.CredentialMode == "per_user",
		sharedOAuth:        sharedOAuth,
		signedIn:           sharedOAuth && s.Credential.CredentialID != "" && s.CredentialState == "active",
		era:                s.Era,
		transport:          transport,
		timeoutMs:          s.TimeoutMs,
		cacheTTLSeconds:    s.ToolsCacheTTLSeconds,
		forwardHeaders:     s.ForwardHeaders,
		propagateDepth:     s.PropagateDepth,
		trustedAnnotations: s.TrustedAnnotations,
		credentialState:    s.CredentialState,
		credentialFP:       credentialFingerprint(s),
		credentialGrant:    pushedGrantIdentity(s.Credential.CredentialID, s.Credential.Secret, s.Auth),
		credentialRevision: s.Credential.Revision,
		credentialRequired: s.CredentialMode != "none" || s.Credential.CredentialID != "" || s.Auth != nil,
		credentialExpiry:   s.Credential.ExpiresAt,
		// The era and the call settings (timeout_ms, forward_headers) reach
		// the client as they do a YAML server's;
		// the factory adds mcp.max_result_bytes.
		client: mcp.ClientConfig{
			ServerID:      id,
			URL:           s.URL,
			TransportType: transport,
			ToolsCacheTTL: time.Duration(s.ToolsCacheTTLSeconds) * time.Second,
			Era:           s.Era,
			Call: mcp.CallSettings{
				Timeout:        time.Duration(s.TimeoutMs) * time.Millisecond,
				ForwardHeaders: s.ForwardHeaders,
			},
		},
	}
	if s.CredentialMode == "shared" && !sp.sharedOAuth && (s.Credential.Secret == nil || s.Credential.Secret.Value == "") && s.Auth == nil && sp.credentialState == "active" {
		sp.credentialState = "needs_reauth"
	}
	if !sp.perUser && !sp.sharedOAuth && sp.credentialState == "active" {
		// A config reaches the pool by the control-plane sync too, which
		// skips the admin PUT's checks: a credential the gateway cannot
		// place exactly as stated is refused here, never sent elsewhere or
		// in part.
		auth, err := sharedAuth(s, cat)
		if err != nil {
			return spec{}, fmt.Errorf("server %q: %w", id, err)
		}
		sp.client.Auth = auth
	}
	fp := map[string]any{
		"url": s.URL, "transport": transport, "era": s.Era,
		"credential": map[string]any{
			"mode": s.CredentialMode, "id": s.Credential.CredentialID, "state": s.CredentialState,
			"header": s.Credential.Header, "secret": secretVersion(s.Credential.Secret, s.Credential.Header),
		},
		"legacy_auth":     legacyAuthVersion(s.Auth),
		"timeout_ms":      s.TimeoutMs,
		"forward_headers": s.ForwardHeaders,
		"propagate_depth": s.PropagateDepth,
	}
	if s.Credential.ExpiresAt != "" {
		fp["credential_expiry"] = s.Credential.ExpiresAt
	}
	if s.CatalogRef != "" {
		fp["catalog_ref"] = s.CatalogRef // only when set, so no existing fingerprint changes
	}
	if s.CredentialKind != "" {
		fp["credential_kind"] = s.CredentialKind // the same
	}
	sp.fp = fingerprint(fp)
	return sp, nil
}

// sharedOAuthOf reports whether s is a shared OAuth server: credential
// kind oauth, on a shared credential only, with no pushed or inline secret,
// since its token is fetched when it is used. Any other kind is refused.
func sharedOAuthOf(s tenant.MCPServerSettings) (bool, error) {
	switch {
	case s.CredentialKind == "":
		return false, nil
	case s.CredentialKind != "oauth":
		return false, fmt.Errorf("credential.kind %q is not oauth", s.CredentialKind)
	case s.CredentialMode != "shared":
		return false, errors.New("credential.kind oauth is for a shared credential only")
	case s.Credential.Secret != nil || s.Auth != nil:
		return false, errors.New("credential.kind oauth: the organization's account token is fetched when it is used, so a pushed or inline secret is refused")
	}
	return true, nil
}

// sharedAuth is a shared server's credential: a pushed secret that states a
// placement, or of a server added from the catalog, as a credential template
// (a header, a query parameter or a secret URL variable, in one part or
// several, with the catalog's constant parts); any other push-only secret
// in the header tenant.MCPSecretHeader names, as before; else the
// legacy inline auth that parses until the vault migration.
func sharedAuth(s tenant.MCPServerSettings, cat credtemplate.Catalog) (mcp.AuthConfig, error) {
	if sec := s.Credential.Secret; sec != nil && sec.Value != "" {
		ts := tenant.TemplateSecret(sec, s.Credential.Header)
		if credtemplate.Applies(ts, s.CatalogRef) {
			tpl, err := credtemplate.ForCatalogSecret(s.URL, ts, cat, s.CatalogRef)
			if err != nil {
				var ce *credtemplate.Error
				if errors.As(err, &ce) && ce.Field != "url" && ce.Field != "catalog_ref" {
					return mcp.AuthConfig{}, fmt.Errorf("credential.secret.%w", err)
				}
				return mcp.AuthConfig{}, err
			}
			return mcp.AuthConfig{Template: tpl}, nil
		}
		header := tenant.MCPSecretHeader(sec, s.Credential.Header)
		if strings.EqualFold(header, "Authorization") {
			return mcp.AuthConfig{Type: "bearer", Token: sec.Value}, nil
		}
		return mcp.AuthConfig{Type: "api_key", Header: header, Key: sec.Value}, nil
	}
	if a := s.Auth; a != nil {
		return mcp.AuthConfig{Type: a.Type, Token: a.Token, Header: a.Header, Key: a.Key}, nil
	}
	return mcp.AuthConfig{}, nil
}

// staticSpecs reads the operator's YAML servers; a server the pool refuses
// (stdio while mcp.stdio.enabled is false, an unknown launcher) gets a load
// error and no client.
func staticSpecs(m config.MCPConfig, redactor *privacy.Redactor) (map[string]spec, map[string]error) {
	specs, errs := map[string]spec{}, map[string]error{}
	for id, sc := range m.Servers {
		cc, err := mcp.StaticPoolClientConfig(id, sc, m, redactor)
		if err != nil {
			errs[id] = err
			continue
		}
		transport := sc.Transport
		if transport == "" {
			transport = "http"
		}
		era := sc.Era
		if era == "" {
			era = "auto"
		}
		specs[id] = spec{
			id: id, source: SourceStatic, client: cc,
			era: era, transport: transport, timeoutMs: sc.TimeoutMS,
			cacheTTLSeconds:    int(sc.ToolsCacheTTL / time.Second),
			forwardHeaders:     sc.ForwardHeaders,
			propagateDepth:     sc.PropagateDepth,
			trustedAnnotations: sc.TrustedAnnotations,
			credentialState:    "active",
			credentialFP:       fingerprint([]any{cc.Auth, sc.Env, sc.EnvFromHost}),
			credentialRequired: sc.Auth.Type != "" && sc.Auth.Type != "none",
			fp: fingerprint(map[string]any{
				"url": sc.URL, "command": sc.Command, "args": sc.Args, "transport": transport, "era": era,
				"auth":            legacyAuthVersion(&tenant.MCPAuthOrgConfig{Type: sc.Auth.Type, Token: sc.Auth.Token, Header: sc.Auth.Header, Key: sc.Auth.Key}),
				"env":             envVersion(sc.Env),
				"env_from_host":   sc.EnvFromHost,
				"timeout_ms":      sc.TimeoutMS,
				"forward_headers": sc.ForwardHeaders,
				"propagate_depth": sc.PropagateDepth,
				"stdio":           []any{m.Stdio.Enabled, m.Stdio.Launcher, m.Stdio.BaseEnv, m.Stdio.Grace, m.Stdio.MaxMessageBytes, m.Stdio.StderrMaxLineBytes},
			}),
		}
	}
	return specs, errs
}

// managedSpecs reads the managed servers an org may use (Deps.ManagedServers,
// ids fagi.<id>). A server the operator marks kind: builtin is served by the
// Deps.Builtins entry of its id, never by a remote client.
func managedSpecs(managed []mcp.ClientConfig, operator map[string]config.ManagedMCPServerConfig) map[string]spec {
	specs := map[string]spec{}
	for _, cc := range managed {
		if cc.ServerID == "" {
			continue
		}
		key, opCfg := operatorEntry(cc.ServerID, operator)
		sp := spec{
			id: cc.ServerID, source: SourceManaged, client: cc,
			era: orDefault(opCfg.Era, "auto"), transport: orDefault(cc.TransportType, "http"),
			credentialState: "active",
		}
		if opCfg.Kind == config.ManagedServerKindBuiltin {
			bare := strings.TrimPrefix(cc.ServerID, "fagi.")
			sp.builtin = cc.ServerID
			sp.builtinIDs = uniq(key, cc.ServerID, bare)
			sp.transport = "builtin"
			sp.fp = fingerprint(map[string]any{"builtin": sp.builtinIDs})
		} else {
			sp.fp = fingerprint(map[string]any{
				"url": cc.URL, "transport": cc.TransportType, "auth": legacyAuthVersion(&tenant.MCPAuthOrgConfig{
					Type: cc.Auth.Type, Token: cc.Auth.Token, Header: cc.Auth.Header, Key: cc.Auth.Key}),
				"era": sp.era,
			})
		}
		specs[cc.ServerID] = sp
	}
	return specs
}

// operatorEntry finds the operator's managed_servers entry of a fagi.<id>
// server, written as "<id>" or "fagi.<id>".
func operatorEntry(serverID string, operator map[string]config.ManagedMCPServerConfig) (string, config.ManagedMCPServerConfig) {
	keys := make([]string, 0, len(operator))
	for k := range operator {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if "fagi."+strings.TrimPrefix(k, "fagi.") == serverID {
			return k, operator[k]
		}
	}
	return "", config.ManagedMCPServerConfig{}
}

func fingerprint(v any) string {
	raw, _ := json.Marshal(v) // maps marshal with sorted keys
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

// secretVersion stands for a pushed secret in a fingerprint without holding
// its value: a rotated secret changes it, and so does its placement (in,
// name, parts), which is added only when set, so a secret pushed as before
// keeps its fingerprint.
func secretVersion(sec *tenant.MCPCredentialSecret, referenceHeader string) string {
	if sec == nil {
		return ""
	}
	fields := []string{sec.Type, sec.Header, sec.Value}
	if v := credtemplate.PlacementVersion(tenant.TemplateSecret(sec, referenceHeader)); v != "" {
		fields = append(fields, v)
	}
	return fingerprint(fields)
}

func legacyAuthVersion(a *tenant.MCPAuthOrgConfig) string {
	if a == nil || (a.Type == "" && a.Token == "" && a.Key == "" && a.Header == "") {
		return ""
	}
	return fingerprint([]string{a.Type, a.Token, a.Header, a.Key})
}

func envVersion(env map[string]string) string {
	if len(env) == 0 {
		return ""
	}
	return fingerprint(env)
}

func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

func uniq(ids ...string) []string {
	var out []string
	for _, id := range ids {
		if id != "" && !slices.Contains(out, id) {
			out = append(out, id)
		}
	}
	return out
}
