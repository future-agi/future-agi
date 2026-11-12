package mcp

import (
	"context"
	"crypto/tls"
	"net/http"
)

// requestCredential is intentionally not a HeaderSource. Once constructed it
// has no operation that returns its credential. Only this file may inspect it;
// HTTP and SSE receive a transport capability, never a reusable bearer.
// AuthConfig's public fields remain inputs for ordinary legacy clients only.
type requestCredential struct {
	bearerHeaders http.Header
	legacy        AuthConfig
	authorize     func(context.Context) error
	check         func(context.Context) error
}

func (c *requestCredential) verify(ctx context.Context) error {
	if c.authorize == nil {
		return CredentialUnavailable()
	}
	if err := c.authorize(ctx); err != nil {
		return err
	}
	var grantErr error
	if c.check != nil {
		grantErr = c.check(ctx)
	}
	// Grant validation may block on distributed epoch I/O. Its successful
	// result cannot carry an earlier admission across a catalog/store change.
	// Keep admission before credential access AND after every grant check;
	// only nonblocking error/context checks remain before delegation.
	if err := c.authorize(ctx); err != nil {
		return err
	}
	if grantErr != nil {
		return grantErr
	}
	return ctx.Err()
}

func (*requestCredential) String() string   { return "[sealed request credential]" }
func (*requestCredential) GoString() string { return "[sealed request credential]" }

// PinnedClientAuth takes custody of a resolved grant's headers. check validates
// that exact grant's lifetime/epoch; GuardClientAuth adds the entry admission.
// Copying the headers prevents a caller from replacing the pinned credential.
func PinnedClientAuth(headers http.Header, check func(context.Context) error) AuthConfig {
	if check == nil {
		check = func(context.Context) error { return CredentialUnavailable() }
	}
	return AuthConfig{requestCredential: &requestCredential{bearerHeaders: headers.Clone(), check: check}}
}

// GuardClientAuth seals all authentication inputs behind authorize. Tenancy
// supplies its entry-bound current-state check, which calls the single policy
// and retires on refusal. The returned config exposes no token, template or
// header provider. No successful admission outcome is retained.
func GuardClientAuth(auth AuthConfig, authorize func(context.Context) error) AuthConfig {
	if authorize == nil {
		authorize = func(context.Context) error { return CredentialUnavailable() }
	}
	if original := auth.requestCredential; original != nil {
		sealed := *original
		sealed.authorize = authorize
		return AuthConfig{requestCredential: &sealed}
	}
	return AuthConfig{requestCredential: &requestCredential{legacy: auth, authorize: authorize}}
}

// installRequestCredential is called only by upstreamHTTPClient, shared by
// HTTP POSTs, notification streams and legacy SSE GET/POSTs. Redials call the
// same builder with the same opaque capability, not a cached authorization.
func installRequestCredential(client *http.Client, auth AuthConfig) {
	if sealed := auth.requestCredential; sealed != nil {
		if transport, ok := client.Transport.(*http.Transport); ok {
			// net/http may replay a request internally on a stale pooled connection,
			// below RoundTrip middleware. Use fresh HTTP/1 connections for governed
			// credentials so a replay cannot reuse an already-attached bearer. MCP
			// streams remain long-lived; protocol reconnects re-enter this boundary.
			transport = transport.Clone()
			transport.DisableKeepAlives = true
			transport.ForceAttemptHTTP2 = false
			transport.TLSNextProto = make(map[string]func(string, *tls.Conn) http.RoundTripper)
			client.Transport = transport
		}
		client.Transport = credentialTransport{next: client.Transport, sealed: sealed}
	}
}

// Governed callers cannot inject a second bearer through per-call options.
// The only attachment happens in credentialTransport after current admission.
func credentialSendOptions(auth AuthConfig, opts sendOptions) sendOptions {
	if auth.requestCredential != nil {
		opts.auth = nil
		opts.header = opts.header.Clone()
		opts.header.Del("Authorization")
	}
	return opts
}

type credentialTransport struct {
	next   http.RoundTripper
	sealed *requestCredential
}

func rejectCredentialRequest(req *http.Request, err error) (*http.Response, error) {
	if req.Body != nil {
		_ = req.Body.Close()
	}
	return nil, err
}

func (t credentialTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// This check precedes a HeaderSource lookup too: a refresh is an
	// authenticated operation, not permission to obtain a fresh grant.
	if err := t.sealed.verify(req.Context()); err != nil {
		return rejectCredentialRequest(req, err)
	}
	cloned := req.Clone(req.Context())
	if cloned.Header == nil {
		cloned.Header = make(http.Header)
	}
	auth := t.sealed.legacy
	switch auth.Type {
	case "bearer":
		cloned.Header.Set("Authorization", "Bearer "+auth.Token)
	case "api_key":
		if auth.Header != "" {
			cloned.Header.Set(auth.Header, auth.Key)
		}
	}
	if auth.Source != nil {
		headers, err := auth.Source.Header(req.Context())
		if err != nil {
			return rejectCredentialRequest(req, err)
		}
		for name, values := range headers {
			cloned.Header[http.CanonicalHeaderKey(name)] = append([]string(nil), values...)
		}
	}
	for name, values := range t.sealed.bearerHeaders {
		cloned.Header[http.CanonicalHeaderKey(name)] = append([]string(nil), values...)
	}
	// Recheck after a possibly slow source refresh or template placement and
	// immediately before the underlying transport. Redirects enter here anew.
	next := credentialDispatch{next: t.next, check: t.sealed.verify}
	var resp *http.Response
	var err error
	if auth.Template != nil {
		resp, err = auth.Template.RoundTripper(next).RoundTrip(cloned)
	} else {
		resp, err = next.RoundTrip(cloned)
	}
	// net/http records the authenticated clone on Response.Request. Restore
	// the caller's credential-free request on every non-nil response (even
	// response-plus-error), including the history exposed during redirects.
	// Template URL/body/error scrubbing has already run inside this boundary.
	if resp != nil {
		resp.Request = req
	}
	return resp, err
}

func (t credentialTransport) CloseIdleConnections() {
	if closer, ok := t.next.(interface{ CloseIdleConnections() }); ok {
		closer.CloseIdleConnections()
	}
}

type credentialDispatch struct {
	next  http.RoundTripper
	check func(context.Context) error
}

func (t credentialDispatch) RoundTrip(req *http.Request) (*http.Response, error) {
	if err := t.check(req.Context()); err != nil {
		return rejectCredentialRequest(req, err)
	}
	return t.next.RoundTrip(req)
}
