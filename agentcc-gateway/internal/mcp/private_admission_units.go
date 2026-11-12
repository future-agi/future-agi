package mcp

import (
	"bytes"
	"net/http"
)

const admissionUnitMaxBytes = 65536

// AdmissionUnit is an unforgeable in-process claim, scoped to one sink and the
// original trusted binding. Runtimes cannot set phase, revision, IDs or target.
// Do not retain claims across Dispatch; no unit state comes from a client.
type AdmissionUnit struct {
	owner   *admissionWriter
	binding LiveAuthorizationRequest
}

// HostedUnit is a COMPLETE bounded logical delivery, not arbitrary transport
// chunks. One list page, one content chunk, one event or one RPC response is one
// unit. Multiple transport writes/flushes of that unit are not new units. Large
// results must be partitioned by the separately qualified protocol runtime;
// pages/chunks/resumed RPCs may never be bundled to evade CONTINUATION.
type HostedUnit struct {
	Status    int
	MediaType string
	Body      []byte
}

// HostedUnitSink exposes no raw HTTP writer. BeginUnit claims once, ReleaseUnit
// freezes/bounds the body and freshly authorizes before any header/body/flush.
// A failed release/short write cannot be retried. No concurrent writes, detached
// goroutines, client-owned phase state or unbounded first unit are supported.
type HostedUnitSink interface {
	BeginUnit() (AdmissionUnit, error)
	ReleaseUnit(AdmissionUnit, HostedUnit) error
}

// Owned lifecycle helpers may use existing JSON encoders only into this bounded
// private buffer. It does not implement Flusher or expose a transport writer.
type admissionResponseBuffer struct {
	body   bytes.Buffer
	header http.Header
	status int
	err    error
}

func (b *admissionResponseBuffer) Header() http.Header { return b.header }
func (b *admissionResponseBuffer) WriteHeader(status int) {
	if b.status == 0 {
		b.status = status
	}
}
func (b *admissionResponseBuffer) Write(p []byte) (int, error) {
	if b.err != nil {
		return 0, b.err
	}
	if len(p) > admissionUnitMaxBytes-b.body.Len() {
		b.err = errAdmissionDenied
		return 0, b.err
	}
	if b.status == 0 {
		b.status = http.StatusOK
	}
	return b.body.Write(p)
}
func (w *admissionWriter) reply(encode func(http.ResponseWriter)) error {
	b := &admissionResponseBuffer{header: make(http.Header)}
	encode(b)
	if b.err != nil {
		w.mu.Lock()
		defer w.mu.Unlock()
		return w.fail(b.err)
	}
	if b.status == 0 {
		b.status = http.StatusOK
	}
	// Only the private lifecycle path may set these protocol headers.
	for _, name := range []string{"MCP-Session-Id", "Cache-Control"} {
		if value := b.header.Get(name); value != "" {
			w.header.Set(name, value)
		}
	}
	media := b.header.Get("Content-Type")
	if media == "application/json; charset=utf-8" {
		media = "application/json"
	}
	unit, err := w.BeginUnit()
	if err != nil {
		return err
	}
	return w.ReleaseUnit(unit, HostedUnit{Status: b.status, MediaType: media, Body: b.body.Bytes()})
}
