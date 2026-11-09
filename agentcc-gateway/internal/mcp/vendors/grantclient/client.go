// Package grantclient implements mcp-vendor-credential-v1. Platform secrets
// stay in Django; every fetch is scoped to org, server, vendor and end user.
package grantclient

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const Path = "/agentcc/mcp/vendors/internal/credential/"
const maxAnswerBytes = 64 << 10

var ErrUnavailable = errors.New("mcp vendor credential service unavailable")

type EndUser struct {
	Source string `json:"source"`
	ID     string `json:"id"`
}
type Request struct {
	OrgID    string  `json:"org_id"`
	ServerID string  `json:"server_id"`
	Vendor   string  `json:"vendor"`
	EndUser  EndUser `json:"end_user"`
	Refresh  *bool   `json:"refresh,omitempty"`
}

// Grant contains secrets. It must never be logged or placed in evidence.
type Grant struct {
	Vendor            string `json:"vendor"`
	AccessToken       string `json:"access_token,omitempty"`
	ExpiresAt         string `json:"expires_at,omitempty"`
	ProjectID         string `json:"project_id,omitempty"`
	Environment       string `json:"environment,omitempty"`
	AppSlug           string `json:"app_slug,omitempty"`
	ExternalUserID    string `json:"external_user_id,omitempty"`
	AccountID         string `json:"account_id,omitempty"`
	APIKey            string `json:"api_key,omitempty"`
	Endpoint          string `json:"endpoint,omitempty"`
	CredentialVersion int    `json:"credential_version"`
}
type Refusal struct{ Reason, ConnectHint string }

func (r *Refusal) Error() string { return "vendor credential refused: " + r.Reason }

type Source interface {
	Fetch(context.Context, Request) (Grant, error)
}
type Options struct {
	BaseURL, Secret string
	AdminTokens     []string
	Transport       http.RoundTripper
	Timeout         time.Duration
}
type Client struct {
	endpoint, secret string
	http             *http.Client
}

func New(o Options) (*Client, error) {
	u, err := url.Parse(strings.TrimRight(o.BaseURL, "/"))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || o.Secret == "" {
		return nil, ErrUnavailable
	}
	for _, admin := range o.AdminTokens {
		if admin != "" && subtle.ConstantTimeCompare([]byte(admin), []byte(o.Secret)) == 1 {
			return nil, ErrUnavailable
		}
	}
	if o.Timeout <= 0 || o.Timeout > 5*time.Second {
		o.Timeout = 5 * time.Second
	}
	transport := o.Transport
	if transport == nil {
		transport = http.DefaultTransport.(*http.Transport).Clone()
	}
	return &Client{endpoint: u.String() + Path, secret: o.Secret, http: &http.Client{Transport: transport, Timeout: o.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (c *Client) Fetch(ctx context.Context, request Request) (Grant, error) {
	if request.OrgID == "" || request.ServerID == "" || request.EndUser.ID == "" || (request.EndUser.Source != "member" && request.EndUser.Source != "asserted") || (request.Vendor != "pipedream" && request.Vendor != "composio") {
		return Grant{}, ErrUnavailable
	}
	raw, err := json.Marshal(request)
	if err != nil {
		return Grant{}, ErrUnavailable
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, bytes.NewReader(raw))
	if err != nil {
		return Grant{}, ErrUnavailable
	}
	req.Header.Set("X-Agentcc-MCP-Internal", c.secret)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	res, err := c.http.Do(req)
	if err != nil {
		return Grant{}, ErrUnavailable
	}
	defer res.Body.Close()
	raw, err = io.ReadAll(io.LimitReader(res.Body, maxAnswerBytes+1))
	if err != nil || res.StatusCode != http.StatusOK || len(raw) > maxAnswerBytes {
		return Grant{}, ErrUnavailable
	}
	var answer struct {
		Grant
		Error       string `json:"error"`
		ConnectHint string `json:"connect_hint"`
	}
	if json.Unmarshal(raw, &answer) != nil {
		return Grant{}, ErrUnavailable
	}
	if answer.Error != "" {
		switch answer.Error {
		case "no_credential", "not_connected", "needs_reauth", "revoked", "expired", "identity_refused":
			return Grant{}, &Refusal{Reason: answer.Error, ConnectHint: answer.ConnectHint}
		}
		return Grant{}, ErrUnavailable
	}
	if answer.Vendor != request.Vendor || answer.CredentialVersion < 1 {
		return Grant{}, ErrUnavailable
	}
	if answer.Vendor == "pipedream" {
		expiry, err := time.Parse(time.RFC3339, answer.ExpiresAt)
		if err != nil || !expiry.After(time.Now()) || !safeValue(answer.AccessToken) || !safeValue(answer.ProjectID) || !safeValue(answer.AppSlug) || !safeValue(answer.ExternalUserID) || (answer.Environment != "development" && answer.Environment != "production") || (answer.AccountID != "" && !safeValue(answer.AccountID)) || answer.APIKey != "" || answer.Endpoint != "" {
			return Grant{}, ErrUnavailable
		}
	} else {
		if !safeValue(answer.APIKey) || !safeValue(answer.Endpoint) || answer.AccessToken != "" {
			return Grant{}, ErrUnavailable
		}
		if answer.ExpiresAt != "" {
			expiry, err := time.Parse(time.RFC3339, answer.ExpiresAt)
			if err != nil || !expiry.After(time.Now()) {
				return Grant{}, ErrUnavailable
			}
		}
	}
	return answer.Grant, nil
}
func safeValue(value string) bool { return value != "" && !strings.ContainsAny(value, "\r\n\x00") }
