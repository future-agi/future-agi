package server

import "github.com/futureagi/agentcc-gateway/internal/models"

// finishHold carries a stream's usage to a client that did not ask for it.
// Such a client gets no chunk with empty choices, so the
// usage the gateway bills rides on the last chunk with choices, the one
// carrying finish_reason, as Mistral's API sends it. The stream holds each
// choice's finish chunk until the upstream ends (the usage is known once the
// post-plugins have billed it), then writes the held chunks in the order
// they came, the last with the usage. No other chunk waits. A provider's own
// usage, on whatever chunk it rides, is metered and never forwarded, so the
// stream carries the usage once.
//
// A client that asked for usage (stream_options.include_usage) or for the
// gateway's metadata gets OpenAI's contract instead, one final chunk with
// empty choices (wantsFinalStreamChunk), and nothing is held.
type finishHold struct {
	on   bool
	held []models.StreamChunk
}

func newFinishHold(rc *models.RequestContext) *finishHold {
	return &finishHold{on: !wantsFinalStreamChunk(rc)}
}

// take reports whether the hold keeps chunk, a chunk the client gets. When a
// finish chunk comes for a choice the hold already has one for (an upstream
// that sends finish_reason more than once), release is every held chunk, in
// order, to be written now, before chunk: the hold keeps at most one chunk
// per choice.
func (f *finishHold) take(chunk models.StreamChunk) (release []models.StreamChunk, held bool) {
	if !f.on || !carriesFinishReason(chunk) {
		return nil, false
	}
	if f.sharesChoice(chunk) {
		release, f.held = f.held, nil
	}
	f.held = append(f.held, chunk)
	return release, true
}

// pending reports whether the hold has a chunk to write.
func (f *finishHold) pending() bool { return len(f.held) > 0 }

// drain empties the hold: the held chunks in the order they came, the last
// carrying usage when it is known (not nil).
func (f *finishHold) drain(usage *models.Usage) []models.StreamChunk {
	out := f.held
	f.held = nil
	if len(out) > 0 && usage != nil {
		out[len(out)-1].Usage = usage
	}
	return out
}

func (f *finishHold) sharesChoice(chunk models.StreamChunk) bool {
	for _, h := range f.held {
		for _, hc := range h.Choices {
			for _, c := range chunk.Choices {
				if hc.Index == c.Index {
					return true
				}
			}
		}
	}
	return false
}

func carriesFinishReason(chunk models.StreamChunk) bool {
	for _, c := range chunk.Choices {
		if c.FinishReason != nil && *c.FinishReason != "" {
			return true
		}
	}
	return false
}
