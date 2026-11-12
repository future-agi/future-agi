package credentials

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// The gateway's channel to Django for users' upstream tokens
// (mcp-upstream-token-v1).
const (
	// UpstreamTokenPath is Django's internal token endpoint.
	UpstreamTokenPath = "/agentcc/internal/mcp/upstream-token/"
	// InternalSecretHeader carries the gateway-to-Django secret
	// (AGENTCC_MCP_DJANGO_SECRET), which is never the admin token.
	InternalSecretHeader = "X-Agentcc-MCP-Internal"

	defaultTokenTimeout = 5 * time.Second
	// maxTokenAnswerBytes bounds the answer read from Django.
	maxTokenAnswerBytes = 64 << 10
)

// Refusal reasons Django answers with (mcp-upstream-token-v1 Refusal.error).
const (
	RefusalNoCredential = "no_credential"
	RefusalNeedsReauth  = "needs_reauth"
	RefusalRevoked      = "revoked"
	RefusalExpired      = "expired"
	// RefusalMicrosoftComingSoon is Microsoft's hold, answered with HTTP
	// 409: Django holds the token back by policy, and connecting again
	// cannot start either.
	RefusalMicrosoftComingSoon = "microsoft_coming_soon"
)

// Refusal is Django's typed answer that the user has no usable token for
// the server: the user must connect, or connect again, or, on Microsoft's
// hold, cannot use it yet.
type Refusal struct {
	Reason string
	// ConnectHint is the start page Django names for connecting, if any.
	ConnectHint string
	// Message is Django's sentence for the caller on Microsoft's hold,
	// made plain and bounded (mcp.RefusalMessage); "" on any other refusal.
	Message string
}

func (r *Refusal) Error() string { return "mcp upstream token refused: " + r.Reason }

// ErrTokenChannel wraps every failure to get an answer from Django: the
// request did not go out, timed out, was refused or was not understood. It
// is never a reason to use another credential.
var ErrTokenChannel = errors.New("mcp credentials: the upstream token service is unavailable")

// TokenClient fetches users' upstream tokens from Django just in time. It
// sends the internal secret only to the configured address: it follows no
// redirect.
type TokenClient struct {
	endpoint string
	secret   string
	http     *http.Client
}

// TokenClientOptions configure a TokenClient.
type TokenClientOptions struct {
	// BaseURL is mcp.django.base_url, operator config.
	BaseURL string
	// Secret is mcp.django.internal_token_secret.
	Secret string
	// AdminTokens are the gateway's admin tokens; the secret must differ
	// from each, so that neither credential stands in for the other.
	AdminTokens []string
	// Timeout bounds each fetch (defaultTokenTimeout when 0).
	Timeout time.Duration
	// Transport carries the requests (a clone of the default when nil).
	Transport http.RoundTripper
}

// NewTokenClient checks o and returns the client.
func NewTokenClient(o TokenClientOptions) (*TokenClient, error) {
	u, err := url.Parse(strings.TrimRight(o.BaseURL, "/"))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("mcp.django.base_url must be an absolute http(s) URL with a host and no credentials, query or fragment")
	}
	if o.Secret == "" {
		return nil, errors.New("mcp.django.internal_token_secret (AGENTCC_MCP_DJANGO_SECRET) is required with mcp.django.base_url")
	}
	for _, admin := range o.AdminTokens {
		if admin != "" && subtle.ConstantTimeCompare([]byte(admin), []byte(o.Secret)) == 1 {
			return nil, errors.New("mcp.django.internal_token_secret must be a secret of its own, not the admin token")
		}
	}
	if o.Timeout <= 0 {
		o.Timeout = defaultTokenTimeout
	}
	rt := o.Transport
	if rt == nil {
		rt = http.DefaultTransport.(*http.Transport).Clone()
	}
	return &TokenClient{
		endpoint: u.String() + UpstreamTokenPath,
		secret:   o.Secret,
		http: &http.Client{
			Transport: rt,
			Timeout:   o.Timeout,
			// A redirect would carry the secret to another address: the
			// answer is taken as it is, and a 3xx is a channel failure.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

// Endpoint is the URL the client posts to.
func (c *TokenClient) Endpoint() string { return c.endpoint }

// tokenRequest is mcp-upstream-token-v1 $defs/Request.
type tokenRequest struct {
	OrgID    string `json:"org_id"`
	UserID   string `json:"user_id"`
	ServerID string `json:"server_id"`
	Refresh  *bool  `json:"refresh,omitempty"`
}

// sharedTokenRequest is mcp-upstream-token-v1 $defs/SharedRequest: the
// org's signed-in account on a server, naming no user.
type sharedTokenRequest struct {
	OrgID    string `json:"org_id"`
	ServerID string `json:"server_id"`
	Scope    string `json:"scope"`
	Refresh  *bool  `json:"refresh,omitempty"`
}

// tokenAnswer holds both answer shapes: Success, or Refusal (error set).
type tokenAnswer struct {
	AccessToken       string `json:"access_token"`
	TokenType         string `json:"token_type"`
	ExpiresAt         string `json:"expires_at"`
	CredentialVersion int    `json:"credential_version"`
	Error             string `json:"error"`
	ConnectHint       string `json:"connect_hint"`
}

// Fetch asks Django for k's current token: a user's, or for the org's
// shared key the org's signed-in account's. It returns the token, a
// *Refusal when Django answers that there is none to use, or an error
// wrapping ErrTokenChannel. No error carries the token or the secret.
func (c *TokenClient) Fetch(ctx context.Context, k Key) (Token, error) {
	return c.fetch(ctx, k, nil)
}

// FetchCurrent rechecks the vault after an in-fetch eviction, without refreshing
// a second time. Django still checks membership, status, origin and hard expiry.
func (c *TokenClient) FetchCurrent(ctx context.Context, k Key) (Token, error) {
	refresh := false
	return c.fetch(ctx, k, &refresh)
}

func (c *TokenClient) fetch(ctx context.Context, k Key, refresh *bool) (Token, error) {
	var request any = tokenRequest{OrgID: k.OrgID, UserID: k.UserID, ServerID: k.ServerID, Refresh: refresh}
	if k.Shared {
		request = sharedTokenRequest{OrgID: k.OrgID, ServerID: k.ServerID, Scope: "shared", Refresh: refresh}
	}
	body, err := json.Marshal(request)
	if err != nil {
		return Token{}, fmt.Errorf("%w: %v", ErrTokenChannel, err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return Token{}, fmt.Errorf("%w: %v", ErrTokenChannel, err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set(InternalSecretHeader, c.secret)
	resp, err := c.http.Do(req)
	if err != nil {
		return Token{}, fmt.Errorf("%w: %s", ErrTokenChannel, requestFailure(err))
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxTokenAnswerBytes+1))
	switch {
	case err != nil:
		return Token{}, fmt.Errorf("%w: reading the answer failed", ErrTokenChannel)
	case resp.StatusCode == http.StatusUnauthorized:
		return Token{}, fmt.Errorf("%w: Django refused the gateway's internal secret (HTTP 401)", ErrTokenChannel)
	case resp.StatusCode == http.StatusConflict && len(raw) <= maxTokenAnswerBytes:
		return decodeHold(raw)
	case resp.StatusCode != http.StatusOK:
		return Token{}, fmt.Errorf("%w: HTTP %d", ErrTokenChannel, resp.StatusCode)
	case len(raw) > maxTokenAnswerBytes:
		return Token{}, fmt.Errorf("%w: the answer is larger than %d bytes", ErrTokenChannel, maxTokenAnswerBytes)
	}
	return decodeTokenAnswer(raw)
}

// decodeTokenAnswer reads one mcp-upstream-token-v1 answer.
func decodeTokenAnswer(raw []byte) (Token, error) {
	var a tokenAnswer
	if err := json.Unmarshal(raw, &a); err != nil {
		return Token{}, fmt.Errorf("%w: the answer is not JSON", ErrTokenChannel)
	}
	if a.Error != "" {
		return Token{}, &Refusal{Reason: a.Error, ConnectHint: a.ConnectHint}
	}
	if a.AccessToken == "" || !strings.EqualFold(a.TokenType, "Bearer") {
		return Token{}, fmt.Errorf("%w: the answer is neither a bearer token nor a refusal", ErrTokenChannel)
	}
	exp, err := time.Parse(time.RFC3339, a.ExpiresAt)
	if err != nil {
		return Token{}, fmt.Errorf("%w: expires_at is not an RFC 3339 time", ErrTokenChannel)
	}
	return Token{AccessToken: a.AccessToken, ExpiresAt: exp, CredentialVersion: a.CredentialVersion}, nil
}

// decodeHold reads an HTTP 409 answer. Microsoft's hold (error
// microsoft_coming_soon) is a refusal carrying only its message for the
// caller: no connect hint, as connecting again cannot start under the hold,
// and no other field. Any other 409 is a failure of the channel.
func decodeHold(raw []byte) (Token, error) {
	var a struct {
		Error   string `json:"error"`
		Message string `json:"message"`
	}
	if json.Unmarshal(raw, &a) != nil || a.Error != RefusalMicrosoftComingSoon {
		return Token{}, fmt.Errorf("%w: HTTP %d", ErrTokenChannel, http.StatusConflict)
	}
	return Token{}, &Refusal{Reason: RefusalMicrosoftComingSoon, Message: mcp.RefusalMessage(a.Message)}
}

// requestFailure names why a request failed without echoing the URL's
// parts that url.Error repeats.
func requestFailure(err error) string {
	var ue *url.Error
	if errors.As(err, &ue) {
		err = ue.Err
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "timed out"
	}
	return err.Error()
}
