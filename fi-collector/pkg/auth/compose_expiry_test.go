package auth

import (
	"context"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func composeExpiredSeedAndProductionPublicationContracts(t *testing.T) {
	t.Run("production-has-no-fixture-seam", func(t *testing.T) {
		paths, err := filepath.Glob("*.go")
		if err != nil || len(paths) == 0 {
			t.Fatal("production source inventory unavailable")
		}
		for _, path := range paths {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			ast.Inspect(file, func(n ast.Node) bool {
				if id, ok := n.(*ast.Ident); ok && id.Name == "putPositive" {
					t.Errorf("test-only seed present in production: %s", path)
				}
				return true
			})
		}
	})
	t.Run("expires-while-publication-waits", func(t *testing.T) {
		_, _, live, _ := composeCredential(t, true)
		candidate := refreshResult()
		claims := *live.Grant
		claims.Exp = time.Now().Unix() + 1
		candidate.Grant = &claims
		c := newCache(30*time.Second, 0)
		c.putPositive("key", live)
		original, _ := c.m.Load("key")
		stamp, epoch, err := c.validationStamp(context.Background(), "key")
		if err != nil {
			t.Fatal(err)
		}
		c.mu.Lock()
		var unlock sync.Once
		defer unlock.Do(c.mu.Unlock)
		base, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		ctx := &composeContext{Context: base, waiting: make(chan struct{})}
		ctx.gate.Store(true)
		type publication struct {
			ok  bool
			err error
		}
		done := make(chan publication, 1)
		go func() {
			ok, err := c.publishValidated(ctx, "key", candidate, epoch, stamp)
			done <- publication{ok, err}
		}()
		composeWait(t, ctx.waiting)
		timer := time.NewTimer(time.Until(time.Unix(claims.Exp, 0)))
		defer timer.Stop()
		<-timer.C
		unlock.Do(c.mu.Unlock)
		select {
		case got := <-done:
			if got.ok || !errors.Is(got.err, ErrUnauthenticated) {
				t.Fatalf("expiry while gate held admitted: %v", got.err)
			}
		case <-base.Done():
			t.Fatal("publication missed original deadline")
		}
		if stored, _ := c.m.Load("key"); stored != original {
			t.Fatal("expired publication changed independent live resident")
		}
		if e, _ := c.peek("key"); e == nil || e.result != live {
			t.Fatal("live control lost")
		}
	})
	_, _, live, _ := composeCredential(t, true)
	expired := refreshResult()
	claims := *live.Grant
	claims.Exp = time.Now().Unix() - 1
	expired.Grant = &claims
	c := newCache(time.Second, 0)
	c.putPositive("key", live)
	c.putPositive("key", expired)
	entry, ok := c.m.Load("key")
	if !ok || entry.(*cacheEntry).result != expired {
		t.Fatal("expired seed failed to overwrite live seed")
	}
	if e, s := c.peek("key"); e != nil || s != "miss" {
		t.Fatal("peek admitted physically resident expired seed")
	}
	if _, ok := c.m.Load("key"); !ok {
		t.Fatal("peek removed candidate before independent get check")
	}
	if e, s := c.get("key"); e != nil || s != "miss" {
		t.Fatal("get admitted expired seed")
	}
	if _, ok := c.m.Load("key"); ok {
		t.Fatal("get did not remove expired observation")
	}
	c.putPositive("key", live)
	stamp, epoch, err := c.validationStamp(context.Background(), "key")
	if err != nil {
		t.Fatal(err)
	}
	published, err := c.publishValidated(context.Background(), "key", expired, epoch, stamp)
	if published || !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("production admitted expired seed contract: %v", err)
	}
	if e, _ := c.peek("key"); e == nil || e.result != live {
		t.Fatal("rejected publication damaged live control")
	}
	published, err = c.publishValidated(context.Background(), "key", nil, epoch, stamp)
	if !published || err != nil {
		t.Fatalf("current nil completion changed class: %v", err)
	}
	if e, _ := c.peek("key"); e == nil || e.result != live {
		t.Fatal("nil completion deleted replacement")
	}
	aged := stamp
	aged.started = time.Now().Add(-2 * time.Second)
	if ok, e := c.publishValidated(context.Background(), "key", live, epoch, aged); ok || e != nil {
		t.Fatalf("aged lookup replenished lifetime: %v", e)
	}
	c.evictAPIKey("uncached")
	if ok, e := c.publishValidated(context.Background(), "key", live, epoch, stamp); ok || e != nil {
		t.Fatalf("fenced lookup published: %v", e)
	}
}

func composeGrantExpiresDuringFlightAndBeforeDelivery(t *testing.T) {
	for _, phase := range []string{"lookup", "delivery", "publication-gate"} {
		t.Run(phase, func(t *testing.T) {
			api, _, result, cfg := composeCredential(t, true)
			fields := sandboxFields(time.Now())
			fields[8] = strconv.FormatInt(time.Now().Unix()+1, 10)
			token := signedGrantFields(fields, goldenPepper)
			claims, err := VerifyCollectorGrant(api, token, cfg.Peppers, cfg.EnvelopeKids, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			result.Grant = claims
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			r := composeResolver{publicationFunc: func(context.Context) (*ResolveResult, error) { return nil, errors.New("unexpected personal resolver") }, grant: func(context.Context, *GrantClaims) (*ResolveResult, error) {
				close(entered)
				<-release
				return result, nil
			}}
			a, err := NewWithResolver(cfg, r, nil, nil)
			if err != nil {
				t.Fatal(err)
			}
			ctx := &composeContext{Context: context.Background(), entered: make(chan struct{}), release: make(chan struct{})}
			if phase == "delivery" {
				ctx.at = 7
			}
			done := make(chan composeAuthOutcome, 1)
			go func() { got, e := a.Authenticate(ctx, api, token); done <- composeAuthOutcome{got, e} }()
			composeWait(t, entered)
			var unlock sync.Once
			if phase == "publication-gate" {
				a.cache.mu.Lock()
				defer unlock.Do(a.cache.mu.Unlock)
			}
			if phase != "lookup" {
				once.Do(func() { close(release) })
			}
			if phase == "delivery" {
				composeWait(t, ctx.entered)
				if e, _ := a.cache.peek(CacheKey(api, token)); e == nil || e.result != result {
					close(ctx.release)
					t.Fatal("delivery barrier preceded publication")
				}
			}
			// Real wall-clock exp, shared by verifier, cache and admission. No
			// grace, rewritten fetchedAt, timeout widening, or auth-mode seam.
			timer := time.NewTimer(time.Until(time.Unix(claims.Exp, 0)))
			defer timer.Stop()
			<-timer.C
			if phase == "lookup" {
				once.Do(func() { close(release) })
			}
			if phase == "delivery" {
				close(ctx.release)
			}
			if phase == "publication-gate" {
				unlock.Do(a.cache.mu.Unlock)
			}
			out := composeOutcome(t, done)
			if out.result != nil || !errors.Is(out.err, ErrUnauthenticated) {
				t.Fatalf("expired result admitted at %s: %v", phase, out.err)
			}
			if e, _ := a.cache.peek(CacheKey(api, token)); e != nil {
				t.Fatal("expired result remained fresh")
			}
		})
	}
}
