package credtemplate

import (
	"mime"
	"net/http"
)

// RoundTripper wraps base (nil: http.DefaultTransport) so that every
// request to the template's origin goes out as a copy carrying the parts;
// a request to any other origin is passed to base unchanged, as the same
// *http.Request. The caller's request is never changed, and the response
// names it (resp.Request). An error of base is returned with every
// spelling of a secret scrubbed from its text, still unwrapping to its
// cause. A Location, and the endpoint event that opens an HTTP+SSE stream,
// come back in the template's form (untemplate), so a client following
// them keeps no secret and the template places the parts on the next hop.
// CloseIdleConnections reaches base.
func (t *Template) RoundTripper(base http.RoundTripper) http.RoundTripper {
	if base == nil {
		base = http.DefaultTransport
	}
	return &roundTripper{t: t, base: base}
}

type roundTripper struct {
	t    *Template
	base http.RoundTripper
}

func (rt *roundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL == nil || origin(req.URL) != rt.t.origin {
		return rt.base.RoundTrip(req)
	}
	out := req.Clone(req.Context())
	if out.Header == nil {
		out.Header = http.Header{}
	}
	rt.t.render(out.URL)
	for _, h := range rt.t.headers {
		out.Header.Set(h.Name, h.Prefix+h.Value)
	}
	resp, err := rt.base.RoundTrip(out)
	if err != nil {
		if resp != nil && resp.Body != nil {
			resp.Body.Close()
		}
		return nil, rt.t.scrubError(err)
	}
	resp.Request = req
	if loc := resp.Header.Get("Location"); loc != "" {
		if clean := rt.t.untemplate(loc); clean != loc {
			resp.Header.Set("Location", clean)
		}
	}
	if opensEventStream(req, resp) {
		// The endpoint event may change length, so the body's length is no
		// longer known.
		resp.Body, resp.ContentLength = newEndpointBody(resp.Body, rt.t), -1
		resp.Header.Del("Content-Length")
	}
	return resp, nil
}

// CloseIdleConnections closes base's idle connections
// (http.Client.CloseIdleConnections finds this method).
func (rt *roundTripper) CloseIdleConnections() {
	if c, ok := rt.base.(interface{ CloseIdleConnections() }); ok {
		c.CloseIdleConnections()
	}
}

// scrubError is err, or, when its text holds a secret, err with that text
// scrubbed.
func (t *Template) scrubError(err error) error {
	msg := err.Error()
	if clean := t.Scrub(msg); clean != msg {
		return &scrubbedError{err: err, msg: clean}
	}
	return err
}

// opensEventStream reports whether resp is a GET's event stream: an
// HTTP+SSE stream, whose first event names its message endpoint.
func opensEventStream(req *http.Request, resp *http.Response) bool {
	if req.Method != http.MethodGet || resp.StatusCode/100 != 2 || resp.Body == nil {
		return false
	}
	mt, _, err := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	return err == nil && mt == "text/event-stream"
}
