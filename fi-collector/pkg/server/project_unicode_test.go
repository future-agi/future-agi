package server

import (
	"bytes"
	"context"
	"testing"
	"time"
)

// Verify real authentication, stamping and enqueueing on both transports, first
// for auto-creation and then with the project already in the key's cache.
func TestAuthenticatedUnicodeResourceProjects(t *testing.T) {
	for _, transport := range []string{"http", "grpc"} {
		for _, name := range []string{"deploy ✈️", "team 👩‍💻", "family 👨‍👩‍👧‍👦", "क्‍ष"} {
			t.Run(transport+"/"+name, func(t *testing.T) {
				r := &ingestResolver{}
				a := realTestAuth(t, r, time.Minute)
				usage := &recordingUsage{}
				s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) { attachRealAuth(s, a); s.usage = usage })
				ctx, cancel := context.WithTimeout(withKeys(context.Background()), 5*time.Second)
				defer cancel()
				body := authenticatedBody(t, name)
				for attempt := 0; attempt < 2; attempt++ {
					if transport == "grpc" {
						if err := exportRaw(conn, ctx, body); err != nil {
							t.Fatalf("attempt %d: %v", attempt, err)
						}
					} else if rec := authPost(s, body); rec.Code != 200 {
						t.Fatalf("attempt %d: HTTP %d: %s", attempt, rec.Code, rec.Body.String())
					}
				}
				if r.creates.Load() != 1 || r.validations.Load() != 1 {
					t.Fatal("second request did not use the existing authenticated project")
				}
				if usage.calls.Load() != 2 || usage.spans.Load() != 2 || usage.wrongOrg.Load() {
					t.Fatal("accepted Unicode project usage missing")
				}
				s.pendMu.Lock()
				for _, row := range s.pend {
					if row["project_id"] != calProject {
						t.Error("accepted Unicode project was not stamped")
					}
				}
				s.pendMu.Unlock()
				s.drainNow(ctx)
				ch.mu.Lock()
				stored := 0
				for _, body := range ch.tables["spans"] {
					stored += bytes.Count(body, []byte("\n"))
				}
				ch.mu.Unlock()
				if stored != 2 {
					t.Fatalf("stored rows = %d, want 2", stored)
				}
			})
		}
	}
}
