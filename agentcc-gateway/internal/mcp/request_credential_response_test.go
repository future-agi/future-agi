package mcp

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

const r7Bearer = "synthetic-r7-response-bearer"
const r7TemplateSecret = "synthetic-r7-template-part"

func r7ResponseAuth(t *testing.T, rawURL string, template bool) AuthConfig {
	t.Helper()
	var auth AuthConfig
	if template {
		tmpl, err := credtemplate.Compile(rawURL, credtemplate.Part{In: credtemplate.InQuery, Name: "signature", Value: r7TemplateSecret})
		if err != nil {
			t.Fatal(err)
		}
		// A legacy bearer plus template demonstrates why the outer boundary
		// must restore its request even though the template restores its own.
		auth = AuthConfig{Type: "bearer", Token: r7Bearer, Template: tmpl}
	} else {
		auth = PinnedClientAuth(http.Header{"Authorization": {"Bearer " + r7Bearer}}, func(context.Context) error { return nil })
	}
	return GuardClientAuth(auth, func(context.Context) error { return nil })
}

func r7CleanRequestChain(t *testing.T, req *http.Request) {
	t.Helper()
	seen := map[*http.Request]bool{}
	for req != nil && !seen[req] {
		seen[req] = true
		if req.Header.Get("Authorization") != "" {
			t.Error("sealed bearer escaped through a caller-visible request")
		}
		if req.URL != nil && strings.Contains(req.URL.String(), r7TemplateSecret) {
			t.Error("template secret escaped through a response request URL")
		}
		if req.Response == nil {
			break
		}
		req = req.Response.Request
	}
}

// Real net/http populates Response.Request with the authenticated request it
// received. CheckRedirect also exposes prior responses through req.Response.
// The server must receive credentials on every hop, but none may escape back
// into the original request, response request or the linked redirect history.
func TestK2R7ResponseCustody(t *testing.T) {
	for _, template := range []bool{false, true} {
		name := "plain"
		if template {
			name = "template"
		}
		t.Run(name, func(t *testing.T) {
			var sends, wrong atomic.Int64
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				sends.Add(1)
				if req.Header.Get("Authorization") != "Bearer "+r7Bearer || (template && req.URL.Query().Get("signature") != r7TemplateSecret) {
					wrong.Add(1)
				}
				next := map[string]string{"/start": "/middle", "/middle": "/done"}[req.URL.Path]
				if next != "" {
					if template {
						next += "?signature=" + r7TemplateSecret
					}
					w.Header().Set("Location", next)
					w.WriteHeader(http.StatusFound)
					return
				}
				_, _ = io.WriteString(w, "synthetic response body")
			}))
			defer srv.Close()
			client := upstreamHTTPClient(nil, r7ResponseAuth(t, srv.URL+"/start", template))
			defer client.CloseIdleConnections()
			redirects := 0
			guard := client.CheckRedirect
			client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
				redirects++
				if req.Response == nil || req.Response.Request == nil {
					t.Error("redirect did not expose a preceding response to exercise custody")
				}
				r7CleanRequestChain(t, req)
				for _, prior := range via {
					r7CleanRequestChain(t, prior)
				}
				return guard(req, via)
			}
			req, err := http.NewRequest(http.MethodGet, srv.URL+"/start", nil)
			if err != nil {
				t.Fatal(err)
			}
			resp, err := client.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			body, err := io.ReadAll(resp.Body)
			if err != nil || string(body) != "synthetic response body" || resp.StatusCode != http.StatusOK {
				t.Fatal("credential boundary changed the response semantics")
			}
			if resp.Request == nil || resp.Request.URL.Path != "/done" {
				t.Fatal("missing final response request")
			}
			r7CleanRequestChain(t, req)
			r7CleanRequestChain(t, resp.Request)
			if sends.Load() != 3 || wrong.Load() != 0 || redirects != 2 {
				t.Fatalf("positive custody control: sends=%d wrong=%d redirects=%d", sends.Load(), wrong.Load(), redirects)
			}
		})
	}
}

type r7RoundTripFunc func(*http.Request) (*http.Response, error)

func (fn r7RoundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) { return fn(req) }

type r7ResponseBody struct {
	io.Reader
	closed bool
}

func (b *r7ResponseBody) Close() error { b.closed = true; return nil }

func TestK2R7DelegatedResponseCustody(t *testing.T) {
	for _, template := range []bool{false, true} {
		for _, fail := range []bool{false, true} {
			name := "plain"
			if template {
				name = "template"
			}
			if fail {
				name += "/response-and-error"
			} else {
				name += "/success"
			}
			t.Run(name, func(t *testing.T) {
				req, _ := http.NewRequest(http.MethodGet, "http://synthetic.invalid/resource", nil)
				auth := r7ResponseAuth(t, req.URL.String(), template)
				sentinel := errors.New("synthetic delegate failure")
				body := &r7ResponseBody{Reader: strings.NewReader("synthetic body")}
				var original *http.Response
				tr := credentialTransport{sealed: auth.requestCredential, next: r7RoundTripFunc(func(out *http.Request) (*http.Response, error) {
					if out == req || out.Header.Get("Authorization") != "Bearer "+r7Bearer {
						t.Error("delegate did not receive a credential-bearing clone")
					}
					original = &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: body, Request: out}
					if fail {
						return original, sentinel
					}
					return original, nil
				})}
				resp, err := tr.RoundTrip(req)
				if fail != errors.Is(err, sentinel) {
					t.Fatal("delegate error identity changed")
				}
				if template && fail {
					// Keep the existing template's error cleanup/scrubbing contract.
					if resp != nil || !body.closed {
						t.Fatal("template error cleanup changed")
					}
				} else {
					if resp == nil || resp != original || resp.Body != body || body.closed {
						t.Fatal("delegate response identity/body changed")
					}
					if resp.Request != req {
						t.Error("response must name the original credential-free caller request")
					}
					r7CleanRequestChain(t, resp.Request)
					_ = resp.Body.Close()
				}
				r7CleanRequestChain(t, req)
			})
		}
	}
}
