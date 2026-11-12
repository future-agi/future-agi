// Package oauthrs makes the gateway's MCP endpoint an OAuth 2.1 resource
// server . Every virtual server is its own
// protected resource: it publishes protected-resource metadata (RFC 9728)
// naming the authorization server, its 401 names that metadata, and it
// accepts only access tokens issued for exactly its own address.
//
// Every address here is built from the deployment's configuration: the
// gateway's public base (mcp.public_url) and the authorization server's
// issuer (mcp.oauth.issuer). Nothing is taken from a request's Host header,
// so a caller cannot make the gateway advertise, or accept tokens for,
// another address.
package oauthrs

import (
	"errors"
	"fmt"
	"net/url"
	"path"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/httppath"
)

// WellKnownPrefix is the path RFC 9728 inserts ahead of a resource's path to
// form its protected-resource metadata address.
const WellKnownPrefix = "/.well-known/oauth-protected-resource"

// The scopes a token may carry on a virtual server.
const (
	ScopeTools     = "mcp:tools"
	ScopeResources = "mcp:resources"
	ScopePrompts   = "mcp:prompts"
)

// defaultEndpoint is the MCP endpoint when mcp.endpoint is unset.
const defaultEndpoint = "/mcp"

// resourceName is the metadata's human-readable name (RFC 9728 resource_name).
const resourceName = "MCP gateway"

// Config is what the resource server is built from.
type Config struct {
	// PublicURL is the gateway's public base (mcp.public_url), such as
	// https://gateway.example.com.
	PublicURL string
	// Endpoint is the MCP endpoint (mcp.endpoint); empty means /mcp.
	Endpoint string
	// Issuer is the authorization server's issuer (mcp.oauth.issuer).
	Issuer string
}

// ConfigFrom reads the resource server's configuration from the MCP config.
func ConfigFrom(m config.MCPConfig) Config {
	return Config{PublicURL: m.PublicURL, Endpoint: m.Endpoint, Issuer: m.OAuth.Issuer}
}

// Resource is the protected resource the MCP endpoint and its virtual
// servers are: their canonical addresses and their metadata.
type Resource struct {
	origin   string // scheme://host[:port] of the public base
	basePath string // the public base's path, "" at the root
	endpoint string // the canonical MCP endpoint, such as /mcp
	issuer   string
}

// NewResource checks c and returns the resource it describes. It refuses a
// configuration that would advertise a relative or ambiguous address: the
// public base and the issuer must each be an absolute http(s) URL with a
// host and no user, query or fragment.
func NewResource(c Config) (*Resource, error) {
	base, err := absoluteURL("mcp.public_url", c.PublicURL)
	if err != nil {
		return nil, err
	}
	if base.RawPath != "" {
		return nil, errors.New("mcp.public_url must not hold percent-encoded path characters")
	}
	basePath := strings.TrimRight(base.Path, "/")
	if basePath != "" && path.Clean(basePath) != basePath {
		return nil, errors.New("mcp.public_url must have a clean path")
	}
	issuer, err := absoluteURL("mcp.oauth.issuer", c.Issuer)
	if err != nil {
		return nil, err
	}
	endpoint := c.Endpoint
	if endpoint == "" {
		endpoint = defaultEndpoint
	}
	return &Resource{
		origin:   base.Scheme + "://" + base.Host,
		basePath: basePath,
		endpoint: httppath.Canonical(endpoint),
		issuer:   issuer.String(),
	}, nil
}

// absoluteURL parses raw as an absolute http(s) URL with a host and no user
// information, query or fragment.
func absoluteURL(name, raw string) (*url.URL, error) {
	if raw == "" {
		return nil, fmt.Errorf("%s is required when mcp.oauth.enabled is true", name)
	}
	u, err := url.Parse(raw)
	switch {
	case err != nil, u.Scheme != "http" && u.Scheme != "https", u.Host == "":
		return nil, fmt.Errorf("%s must be an absolute http(s) URL", name)
	case u.User != nil, u.RawQuery != "", u.ForceQuery, u.Fragment != "", strings.Contains(raw, "#"):
		return nil, fmt.Errorf("%s must not carry user information, a query or a fragment", name)
	}
	return u, nil
}

// Issuer is the authorization server whose tokens the resource accepts.
func (r *Resource) Issuer() string { return r.issuer }

// Endpoint is the canonical MCP endpoint path the gateway serves.
func (r *Resource) Endpoint() string { return r.endpoint }

// resourcePath is the path of the resource for vsID ("" is the endpoint
// itself), as the gateway serves it.
func (r *Resource) resourcePath(vsID string) string {
	if vsID == "" {
		return r.endpoint
	}
	return r.endpoint + "/v/" + url.PathEscape(vsID)
}

// ResourceURI is the canonical address (RFC 8707) of the virtual server vsID,
// or of the endpoint itself when vsID is "": the value an access token's aud
// must equal.
func (r *Resource) ResourceURI(vsID string) string {
	return r.origin + r.basePath + r.resourcePath(vsID)
}

// MetadataURL is the address of the protected-resource metadata of vsID (""
// is the endpoint itself), formed as RFC 9728 section 3.1 forms it: the
// well-known prefix inserted between the host and the resource's path.
func (r *Resource) MetadataURL(vsID string) string {
	return r.origin + WellKnownPrefix + r.basePath + r.resourcePath(vsID)
}

// ResourceForPath maps the path a request was served on to the resource it
// addresses: the endpoint, or one virtual server under it. Any other path
// addresses no resource, and no token is valid on it.
func (r *Resource) ResourceForPath(p string) (string, bool) {
	if p == r.endpoint {
		return r.ResourceURI(""), true
	}
	vsID, ok := strings.CutPrefix(p, r.endpoint+"/v/")
	if !ok || vsID == "" || strings.Contains(vsID, "/") {
		return "", false
	}
	return r.ResourceURI(vsID), true
}

// Metadata is a protected-resource metadata document (RFC 9728 section 2).
type Metadata struct {
	Resource               string   `json:"resource"`
	AuthorizationServers   []string `json:"authorization_servers"`
	ScopesSupported        []string `json:"scopes_supported"`
	BearerMethodsSupported []string `json:"bearer_methods_supported"`
	ResourceName           string   `json:"resource_name"`
}

// Metadata is the protected-resource metadata of vsID ("" is the endpoint
// itself). It depends on the configuration and the id only: the document of
// a virtual server that does not exist has the same shape as one that does,
// so the unauthenticated metadata routes say nothing about which exist.
func (r *Resource) Metadata(vsID string) Metadata {
	return Metadata{
		Resource:               r.ResourceURI(vsID),
		AuthorizationServers:   []string{r.issuer},
		ScopesSupported:        []string{ScopeTools, ScopeResources, ScopePrompts},
		BearerMethodsSupported: []string{"header"},
		ResourceName:           resourceName,
	}
}
