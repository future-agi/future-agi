package media

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
)

func limits() Limits { return Limits{MaxBytes: 1024 * 1024, Deadline: time.Second, MaxPixels: 4096} }
func requireCode(t *testing.T, err error, status int, code string) {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) || e.Status != status || e.Code != code {
		t.Fatalf("error = %v, want %d %s", err, status, code)
	}
}
func TestFetch_LoopbackRefused(t *testing.T) {
	_, err := NewFetcher(nil).Fetch(context.Background(), "https://127.0.0.1/a", "image/png", "", limits())
	requireCode(t, err, 400, "media_url_refused")
}
func TestFetch_MetadataRefused(t *testing.T) {
	_, err := NewFetcher(nil).Fetch(context.Background(), "https://169.254.169.254/a", "image/png", "", limits())
	requireCode(t, err, 400, "media_url_refused")
}
func TestFetch_UserinfoRejected(t *testing.T) {
	_, err := NewFetcher(nil).Fetch(context.Background(), "https://user:secret@example.com/a", "image/png", "", limits())
	requireCode(t, err, 400, "media_url_refused")
	if strings.Contains(err.Error(), "secret") {
		t.Fatal("secret leaked")
	}
}

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestFetch_RedirectToPrivateRefused(t *testing.T) {
	f := NewFetcher(nil)
	guarded := f.client.Transport
	f.client.Transport = transportFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Host == "public.example" {
			return &http.Response{StatusCode: 302, Header: http.Header{"Location": []string{"https://10.0.0.1/a"}}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
		}
		return guarded.RoundTrip(r)
	})
	_, err := f.Fetch(context.Background(), "https://public.example/a", "image/png", "", limits())
	requireCode(t, err, 400, "media_url_refused")
}
func TestFetch_DNSRebindRefused(t *testing.T) {
	var private atomic.Bool
	resolver := &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
		client, server := net.Pipe()
		go func() {
			defer server.Close()
			var size [2]byte
			if _, e := io.ReadFull(server, size[:]); e != nil {
				return
			}
			q := make([]byte, binary.BigEndian.Uint16(size[:]))
			if _, e := io.ReadFull(server, q); e != nil {
				return
			}
			end := 12
			for end < len(q) && q[end] != 0 {
				end += int(q[end]) + 1
			}
			end += 5
			if end > len(q) {
				return
			}
			a := append([]byte(nil), q[:end]...)
			a[2] = 0x81
			a[3] = 0x80
			a[6] = 0
			a[7] = 0
			if binary.BigEndian.Uint16(q[end-4:end-2]) == 1 {
				a[7] = 1
				ip := []byte{203, 0, 113, 9}
				if private.Load() {
					ip = []byte{127, 0, 0, 1}
				}
				a = append(a, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
				a = append(a, ip...)
			}
			binary.BigEndian.PutUint16(size[:], uint16(len(a)))
			server.Write(append(size[:], a...))
		}()
		return client, nil
	}}
	ips, err := resolver.LookupIP(context.Background(), "ip4", "rebind.example")
	if err != nil || len(ips) != 1 || !ips[0].Equal(net.ParseIP("203.0.113.9")) {
		t.Fatal(ips, err)
	}
	private.Store(true)
	f := NewFetcher(nil)
	f.client.Transport.(*http.Transport).DialContext = netguard.DialContext(net.Dialer{Resolver: resolver, Timeout: time.Second}, false)
	_, err = f.Fetch(context.Background(), "https://rebind.example/a", "image/png", "", limits())
	requireCode(t, err, 400, "media_url_refused")
}
func TestFetch_NoAuthForwarded(t *testing.T) {
	data := videotest.PNG()
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
			t.Error("credentials forwarded")
		}
		w.Write(data)
	}))
	defer s.Close()
	f := NewFetcher(nil)
	f.client.Transport = s.Client().Transport
	got, err := f.Fetch(context.Background(), s.URL, "image/jpeg", "", limits())
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(data)
	if !bytes.Equal(got.Data, data) || got.Digest != hex.EncodeToString(sum[:]) || got.Width != 64 || got.Height != 64 || got.ContentType != "image/png" {
		t.Fatalf("bad result %+v", got)
	}
}
func TestFetch_Oversize413(t *testing.T) {
	f := NewFetcher(nil)
	l := limits()
	l.MaxBytes = 8
	_, err := f.Verify(context.Background(), bytes.NewReader(videotest.PNG()), "image/png", "", l)
	requireCode(t, err, 413, "media_too_large")
}
func TestFetch_MIMEMismatch(t *testing.T) {
	_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(videotest.PNG()), "video/mp4", "", limits())
	requireCode(t, err, 400, "media_type_mismatch")
}
func TestFetch_RedirectLimitAndDeadline(t *testing.T) {
	for _, mode := range []string{"redirect", "slow"} {
		t.Run(mode, func(t *testing.T) {
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "slow" {
					<-r.Context().Done()
					return
				}
				http.Redirect(w, r, "/again", 302)
			}))
			defer s.Close()
			f := NewFetcher(nil)
			f.client.Transport = s.Client().Transport
			l := limits()
			l.Deadline = 30 * time.Millisecond
			_, err := f.Fetch(context.Background(), s.URL, "image/png", "", l)
			if mode == "slow" {
				requireCode(t, err, 408, "media_fetch_timeout")
			} else {
				requireCode(t, err, 400, "media_url_refused")
			}
		})
	}
}
func TestVerify_StreamsAndAbortsSink(t *testing.T) {
	for _, valid := range []bool{true, false} {
		t.Run(map[bool]string{true: "valid", false: "invalid"}[valid], func(t *testing.T) {
			data := videotest.MP4()
			if !valid {
				data = data[:len(data)-8]
			}
			committed := false
			var stored []byte
			f := NewFetcher(func(ctx context.Context, key string, r io.Reader) error {
				var err error
				stored, err = io.ReadAll(r)
				committed = err == nil
				return err
			})
			got, err := f.Verify(context.Background(), bytes.NewReader(data), "video/mp4", "video/org/job/0", limits())
			if valid {
				if err != nil || !committed || len(got.Data) != 0 || !bytes.Equal(stored, data) || got.DecodedCheck != "container_only" {
					t.Fatal(got, err, committed)
				}
			} else if err == nil || committed {
				t.Fatal("invalid container committed")
			}
		})
	}
}
