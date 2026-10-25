package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// generationHolder holds the generation recorder, embedded in Handlers. It
// is nil until SetGenerationRecorder installs the record function of the
// store GET /v1/generation reads, and the request path takes no snapshot
// while it is nil.
type generationHolder struct {
	generationRecorder func(snap *models.TraceSnapshot)
}

// SetGenerationRecorder installs the generation store's record function.
// Call it before the server serves.
func (h *Handlers) SetGenerationRecorder(record func(snap *models.TraceSnapshot)) {
	h.generationRecorder = record
}

// recordGeneration is call-order step 20: it stores the generation record of
// a finished request. It runs on its own goroutine with a snapshot, after the
// response is written. Keep observeHook as its first call.
func (h *Handlers) recordGeneration(snap *models.TraceSnapshot) {
	h.observeHook(hookRecordGeneration, nil)
	if h.generationRecorder != nil {
		h.generationRecorder(snap)
	}
}

// recordGenerationFor snapshots rc and records its generation in the
// background, when a recorder is installed.
func (h *Handlers) recordGenerationFor(rc *models.RequestContext) {
	if h.generationRecorder == nil {
		return
	}
	go h.recordGeneration(rc.Snapshot())
}
