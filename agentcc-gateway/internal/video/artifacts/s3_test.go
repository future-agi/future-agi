package artifacts

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestS3_SignedStreamingAndRange(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/video/org/job/0.mp4" {
			t.Error(r.URL.Path)
		}
		auth := r.Header.Get("Authorization")
		for _, v := range []string{"AWS4-HMAC-SHA256 Credential=test/", "SignedHeaders=", "x-amz-content-sha256", "x-amz-date"} {
			if !strings.Contains(auth, v) {
				t.Error("missing signed header", v)
			}
		}
		if r.Header.Get("X-Amz-Date") == "" {
			t.Error("unsigned")
		}
		switch r.Method {
		case "PUT":
			b, err := io.ReadAll(r.Body)
			if err != nil || string(b) != "0123456789" || r.ContentLength != 10 {
				t.Error(string(b), err, r.ContentLength)
			}
			if r.Header.Get("X-Amz-Acl") != "private" || r.Header.Get("X-Amz-Server-Side-Encryption") != "AES256" || !strings.Contains(auth, "x-amz-acl") || !strings.Contains(auth, "x-amz-server-side-encryption") {
				t.Error("private/SSE headers not signed")
			}
			w.WriteHeader(200)
		case "HEAD":
			w.Header().Set("Content-Length", "10")
			w.Header().Set("Content-Type", "video/mp4")
		case "GET":
			if r.Header.Get("Range") != "bytes=2-4" {
				t.Error("missing range")
			}
			w.Header().Set("Content-Length", "3")
			w.Header().Set("Content-Range", "bytes 2-4/10")
			w.WriteHeader(206)
			io.WriteString(w, "234")
		case "DELETE":
			w.WriteHeader(204)
		default:
			t.Error(r.Method)
		}
	}))
	defer server.Close()
	s, err := NewS3(S3Config{Endpoint: server.URL, Bucket: "test", Prefix: "video/", Region: "us-east-1", AccessKey: "test", SecretKey: "test-secret", SSE: "AES256", MaxBytes: 20, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	m, err := s.Put(ctx, "video/org/job/0.mp4", strings.NewReader("0123456789"), Meta{ContentType: "video/mp4"})
	if err != nil || m.Bytes != 10 {
		t.Fatal(m, err)
	}
	m, err = s.Stat(ctx, "video/org/job/0.mp4")
	if err != nil || m.Bytes != 10 {
		t.Fatal(m, err)
	}
	r, err := s.Open(ctx, "video/org/job/0.mp4", "bytes=2-4")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(r.Body)
	r.Body.Close()
	if string(b) != "234" || r.ContentRange != "bytes 2-4/10" {
		t.Fatal(string(b), r)
	}
	if err = s.Delete(ctx, "video/org/job/0.mp4"); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 4 {
		t.Fatal(calls.Load())
	}
}
func TestS3_PrefixOwnershipDenied(t *testing.T) {
	s, err := NewS3(S3Config{Bucket: "b", Prefix: "video/org/", Region: "r", AccessKey: "test", SecretKey: "test", MaxBytes: 10})
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"other/x", "video/org-other/0", "video/org/../evil", "video/org/%2e%2e/x", "/video/org/x", "video/org/"} {
		t.Run(fmt.Sprintf("%q", k), func(t *testing.T) {
			if _, e := s.Put(context.Background(), k, strings.NewReader("x"), Meta{}); !errors.Is(e, ErrKey) {
				t.Fatal(e)
			}
			if _, e := s.Open(context.Background(), k, ""); !errors.Is(e, ErrKey) {
				t.Fatal(e)
			}
			if _, e := s.Stat(context.Background(), k); !errors.Is(e, ErrKey) {
				t.Fatal(e)
			}
			if e := s.Delete(context.Background(), k); !errors.Is(e, ErrKey) {
				t.Fatal(e)
			}
		})
	}
}
func TestS3_FailedReadNeverUploads(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer srv.Close()
	s, e := NewS3(S3Config{Endpoint: srv.URL, Bucket: "b", Prefix: "video/", Region: "r", AccessKey: "test", SecretKey: "test", MaxBytes: 4})
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		r    io.Reader
		want error
	}{{strings.NewReader("12345"), ErrTooLarge}, {brokenReader{}, io.ErrUnexpectedEOF}} {
		if _, e := s.Put(context.Background(), "video/x", tc.r, Meta{}); !errors.Is(e, tc.want) {
			t.Fatal(e)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("uploaded failed input")
	}
}
