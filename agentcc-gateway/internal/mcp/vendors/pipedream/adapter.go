// Package pipedream translates authorized grants to the documented MCP headers.
// No platform client secret, query binding or inbound Authorization is used.
package pipedream

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors/grantclient"
)

type Adapter struct {
	Source     grantclient.Source
	Codec      *mcp.StateCodec
	DjangoBase string
	// Assertions are enabled only by an exact org/server binding. Django applies
	// the authoritative identity_policy too; no client header can opt itself in.
	AssertedBindings map[string]bool
}

func (a *Adapter) AllowAsserted(cc *mcp.CallContext) bool {
	return a.AssertedBindings[cc.Principal.OrgID+"/"+cc.Upstream.ServerID]
}
func (a *Adapter) Resolve(cc *mcp.CallContext, vendor string) (*mcp.AuthOverride, *mcp.Denial) {
	identity, ok := mcp.EndUserOf(cc)
	if vendor != "pipedream" || !ok || a.Source == nil || cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return nil, mcp.CredentialUnavailable()
	}
	sc := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if sc == nil || sc.URL != "https://remote.mcp.pipedream.net/v3" {
		return nil, mcp.CredentialUnavailable()
	}
	grant, err := a.Source.Fetch(cc.Context(), grantclient.Request{OrgID: cc.Principal.OrgID, ServerID: cc.Upstream.ServerID, Vendor: vendor, EndUser: grantclient.EndUser{Source: identity.Source, ID: identity.Subject}})
	if err != nil {
		var refusal *grantclient.Refusal
		if !errors.As(err, &refusal) {
			return nil, mcp.CredentialUnavailable()
		}
		if refusal.Reason == grantclient.ReasonMicrosoftComingSoon {
			// Microsoft's hold: no connect link, as a sign-in cannot start.
			return nil, mcp.CredentialHeld(refusal.Message)
		}
		link := ""
		if identity.Source == "member" && a.Codec != nil && a.DjangoBase != "" {
			base := strings.TrimRight(a.DjangoBase, "/") + "/agentcc/mcp/vendor-connections/" + url.PathEscape(cc.Upstream.ServerID) + "/start/"
			link, _ = credentials.ConnectURL(a.Codec, base, credentials.Key{OrgID: cc.Principal.OrgID, UserID: identity.Subject, ServerID: cc.Upstream.ServerID})
		} else if identity.Source == "asserted" {
			link = AllowedConnectLink(refusal.ConnectHint)
		}
		return nil, mcp.AuthRequired(cc.Upstream.ServerID, refusal.Reason, link)
	}
	headers, err := Headers(grant)
	if err != nil {
		return nil, mcp.CredentialUnavailable()
	}
	return &mcp.AuthOverride{Header: headers}, nil
}
func Headers(grant grantclient.Grant) (http.Header, error) {
	expiry, err := time.Parse(time.RFC3339, grant.ExpiresAt)
	if err != nil || !expiry.After(time.Now()) || grant.Vendor != "pipedream" || grant.CredentialVersion < 1 || (grant.Environment != "development" && grant.Environment != "production") {
		return nil, grantclient.ErrUnavailable
	}
	values := map[string]string{"Authorization": "Bearer " + grant.AccessToken, "x-pd-project-id": grant.ProjectID, "x-pd-environment": grant.Environment, "x-pd-external-user-id": grant.ExternalUserID, "x-pd-app-slug": grant.AppSlug}
	if grant.AccessToken == "" {
		return nil, grantclient.ErrUnavailable
	}
	if grant.AccountID != "" {
		values["x-pd-account-id"] = grant.AccountID
	}
	headers := make(http.Header, len(values))
	for name, value := range values {
		if value == "" || strings.ContainsAny(value, "\r\n\x00") {
			return nil, grantclient.ErrUnavailable
		}
		headers.Set(name, value)
	}
	return headers, nil
}
func AllowedConnectLink(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") {
		return ""
	}
	switch u.Hostname() {
	case "pipedream.com", "connect.pipedream.com":
		return u.String()
	}
	return ""
}
