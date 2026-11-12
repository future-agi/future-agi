package mcp

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

func (s *Server) handleAdmittedGet(w http.ResponseWriter, r *http.Request) {
	h, a, revision, err := s.admissionRequest(r, "session/resume", nil)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	sess, err := s.resumeAdmissionSession(r.Context(), h, r, a, revision)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	op, err := s.beginAdmissionOperation(r.Context(), h, a, revision, LeaseStream, sess)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer op.close()
	r = r.WithContext(op.watch.ctx)
	aw, err := newAdmissionWriter(w, r, op.watch)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer aw.close()
	stream, ok := s.sessions.hub.registerLimited(sess.hash, h.Limits.StreamsPerSession)
	if !ok {
		writeAdmissionRefusal(w, errAdmissionLimit)
		return
	}
	defer s.sessions.hub.unregister(sess.hash, stream)
	last := r.Header.Get("Last-Event-ID")
	if last != "" {
		if _, _, ok := parseEventID(last); !ok || len(last) > 512 {
			return
		}
	}
	aw.header.Set("Cache-Control", "no-cache")
	if admissionStreamFrame(aw, []byte(": connected\n\n")) != nil {
		return
	}
	// The event log/hub is keyed by the scoped session hash. There is still no
	// global event tap or unqualified producer wired into hosted sessions.
	var emitMu sync.Mutex
	emit := func(id string, data []byte) error {
		emitMu.Lock()
		defer emitMu.Unlock()
		if id != "" {
			if _, _, ok := parseEventID(id); !ok || len(id) > 512 {
				return errAdmissionDenied
			}
			if last != "" {
				if newer, ok := eventIDLess(last, id); !ok || !newer {
					return nil
				}
			}
		}
		if err := s.releaseAdmissionEvent(w, r, h, revision, id, data, aw); err != nil {
			op.watch.cancel()
			return err
		}
		if id != "" {
			last = id
		}
		return nil
	}
	if last != "" {
		log, ok := s.sessionStore().(SessionEventLog)
		if !ok {
			return
		}
		var replayErr error
		ctx, cancel := admissionReadContext(r.Context())
		// The store contract must invoke callbacks synchronously and serially.
		err = log.SessionEventsAfter(ctx, sess.key+":events", last, sessionEventLogMax, func(id string, data []byte) {
			if replayErr != nil {
				return
			}
			if ctx.Err() != nil {
				replayErr = errAdmissionUnavailable
			} else {
				replayErr = emit(id, data)
			}
			if replayErr != nil {
				cancel()
			}
		})
		cancel()
		if err != nil || replayErr != nil {
			return
		}
	}
	keepalive := time.NewTicker(streamKeepAlive)
	defer keepalive.Stop()
	for {
		select {
		case <-op.watch.ctx.Done():
			return
		case <-sess.watch.ctx.Done():
			return
		case <-stream.done:
			return
		case <-keepalive.C:
			if admissionStreamFrame(aw, []byte(": keepalive\n\n")) != nil {
				return
			}
		case ev := <-stream.ch:
			if emit(ev.id, ev.data) != nil {
				return
			}
		}
	}
}

// A list-change event is its own CONTINUATION under the correct list action and
// a freshly resolved scoped target, NEVER a resource-list grant reused for a
// prompt notification. Every event gets fresh authorization before release.
func (s *Server) releaseAdmissionEvent(w http.ResponseWriter, r *http.Request, h *HostedAdmission, original MembershipRevision, id string, data []byte, parent ...*admissionWriter) error {
	projected, err := admissionMetadataEvent(data)
	if err != nil {
		return err
	}
	var event struct {
		Method string `json:"method"`
	}
	if json.Unmarshal(projected, &event) != nil {
		return errAdmissionDenied
	}
	method := "resources/list"
	if event.Method == "notifications/prompts/list_changed" {
		method = "prompts/list"
	}
	current, a, revision, err := s.admissionRequest(r, method, nil)
	if err != nil {
		return err
	}
	if current != h || revision != original || a.Phase != PhaseContinuation {
		return errAdmissionDenied
	}
	watch := newAdmissionWatch(r.Context(), a, original, func() LiveAuthorizer { return s.admissionAuthorizer(h) }, admissionRealTicker{time.NewTicker(admissionTick)}, nil)
	defer watch.close()
	var deadline *admissionWriteDeadline
	if len(parent) != 0 {
		deadline = parent[0].deadline
	}
	aw, err := newAdmissionWriterWithDeadline(w, r, watch, deadline)
	if err != nil {
		return err
	}
	defer aw.close()
	frame := []byte("data: ")
	if id != "" {
		if _, _, ok := parseEventID(id); !ok || len(id) > 512 {
			return errAdmissionDenied
		}
		frame = []byte("id: " + id + "\ndata: ")
	}
	frame = append(frame, projected...)
	frame = append(frame, '\n', '\n')
	return admissionStreamFrame(aw, frame)
}
func admissionStreamFrame(w HostedUnitSink, frame []byte) error {
	unit, err := w.BeginUnit()
	if err != nil {
		return err
	}
	return w.ReleaseUnit(unit, HostedUnit{Status: http.StatusOK, MediaType: "text/event-stream", Body: frame})
}
