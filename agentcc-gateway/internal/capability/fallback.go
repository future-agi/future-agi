package capability

import (
	"context"
	"errors"
	"net"
	"net/http"
)

// Class is a capability's fallback class.
type Class string

const (
	// ClassStatelessCrossVendor (search, fetch, document parse, rerank order,
	// guard checks) falls back to the next vendor on 429, 5xx or a timeout
	// only; a 4xx caused by the input never falls back.
	ClassStatelessCrossVendor Class = "stateless_cross_vendor"
	// ClassSameModelOnly (embeddings, TTS voices) never fails over until a
	// parity probe proves two hosts serve the same weights.
	ClassSameModelOnly Class = "same_model_only"
	// ClassCreateTimeOnly (browser, streaming speech, realtime, media jobs)
	// falls back only before an upstream session or job id exists.
	ClassCreateTimeOnly Class = "create_time_only"
	// ClassNone (connector platforms, memory stores) never falls back: a
	// user's grant or data lives in one vendor.
	ClassNone Class = "none"
)

// MaxCandidates is the most vendors one call tries.
const MaxCandidates = 4

func (c Class) valid() bool {
	switch c {
	case ClassStatelessCrossVendor, ClassSameModelOnly, ClassCreateTimeOnly, ClassNone:
		return true
	}
	return false
}

// fallsBack reports whether a call of class that failed with err may move to
// the next candidate. ctx is the call's context: once it has ended (the
// client left or the call's timeout passed) nothing more is tried. The rules
// that hold for every class (never from a managed call, never from or to a
// no_divert vendor, never from BYOK to managed without the org's flag) are
// the selector's and Do's.
func (c Class) fallsBack(ctx context.Context, err error) bool {
	if ctx.Err() != nil {
		return false
	}
	switch c {
	case ClassStatelessCrossVendor:
		return transient(err)
	case ClassCreateTimeOnly:
		var ve *VendorError
		if errors.As(err, &ve) && ve.SessionCreated {
			return false
		}
		return transient(err)
	default:
		return false
	}
}

// transient reports whether err is a vendor failure worth another vendor: a
// 429, a 5xx, a timeout or a transport failure. A 4xx is the input's fault
// and would fail the same way anywhere.
func transient(err error) bool {
	var ve *VendorError
	if errors.As(err, &ve) {
		return ve.Status == 0 || ve.Status == http.StatusTooManyRequests || ve.Status >= 500
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var netErr net.Error
	return errors.As(err, &netErr)
}

// vendorAnswered reports whether the vendor answered the failed call with a
// status of its own, so the call may have been billed upstream.
func vendorAnswered(err error) (*VendorError, bool) {
	var ve *VendorError
	if errors.As(err, &ve) && ve.Status > 0 {
		return ve, true
	}
	return nil, false
}
