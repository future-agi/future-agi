package credentials

import (
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// The connect link an auth_required answer carries (MCP-PLAN M25, typed
// result): M25x's start page with a binding the gateway sealed under the
// state codec's connect purpose. The binding names the org, the server and
// the user the gateway resolved, and expires after ConnectBindingTTL; it
// grants nothing by itself, since the start page also requires that user's
// own sign-in.
const (
	ConnectBindingTTL = 10 * time.Minute
	// ConnectBindingParam is the start page's query parameter.
	ConnectBindingParam = "binding"
)

// ConnectBinding is the sealed payload of a connect link (version 1).
type ConnectBinding struct {
	V        int    `json:"v"`
	OrgID    string `json:"org_id"`
	ServerID string `json:"server_id"`
	UserID   string `json:"user_id"`
}

// ConnectStartPath is M25x's start page for serverID.
func ConnectStartPath(serverID string) string {
	return "/agentcc/mcp/connect/" + url.PathEscape(serverID) + "/start/"
}

var errConnectBase = errors.New("mcp credentials: the connect page is not an absolute http(s) URL")

// ConnectURL is base with k's sealed binding in its query. base is the
// start page Django named (connect_hint), else the server's start page on
// the Django address.
func ConnectURL(codec *mcp.StateCodec, base string, k Key) (string, error) {
	u, err := url.Parse(base)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return "", errConnectBase
	}
	raw, err := json.Marshal(ConnectBinding{V: 1, OrgID: k.OrgID, ServerID: k.ServerID, UserID: k.UserID})
	if err != nil {
		return "", err
	}
	sealed, err := codec.Seal(mcp.StatePurposeConnect, raw, ConnectBindingTTL)
	if err != nil {
		return "", err
	}
	q := u.Query()
	q.Set(ConnectBindingParam, sealed)
	u.RawQuery = q.Encode()
	u.Fragment = ""
	return u.String(), nil
}

// ConnectCodec is the codec connect links are sealed with: keyed on the
// secret the gateway shares with Django (mcp.django.internal_token_secret,
// AGENTCC_MCP_DJANGO_SECRET), from which Django's start page derives the key
// it opens a binding with, so the person a link was made for can start their
// connect with it.
func ConnectCodec(djangoSecret string) (*mcp.StateCodec, error) {
	if djangoSecret == "" {
		return nil, errors.New("mcp credentials: connect links need the secret shared with Django")
	}
	return mcp.NewStateCodec([]string{djangoSecret}, false)
}

// DefaultConnectBase is serverID's start page on the Django address, for a
// refusal that names none.
func DefaultConnectBase(djangoBaseURL, serverID string) string {
	if djangoBaseURL == "" {
		return ""
	}
	return strings.TrimRight(djangoBaseURL, "/") + ConnectStartPath(serverID)
}

// OpenConnectBinding opens a connect link's binding (the start page's check,
// and tests').
func OpenConnectBinding(codec *mcp.StateCodec, sealed string) (ConnectBinding, error) {
	raw, err := codec.Open(mcp.StatePurposeConnect, sealed)
	if err != nil {
		return ConnectBinding{}, err
	}
	var b ConnectBinding
	if err := json.Unmarshal(raw, &b); err != nil || b.V != 1 {
		return ConnectBinding{}, mcp.ErrStateInvalid
	}
	return b, nil
}
