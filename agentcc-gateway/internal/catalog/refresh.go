package catalog

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Source is a managed provider whose catalog the refresher keeps current.
// The gateway's wiring resolves each one against config.yaml
// (sources.Resolve), attaching the reader its catalog_source needs; the
// readers live in the sources subpackage, which imports this one.
type Source struct {
	ProviderID string
	Type       string // the config.yaml type; "openrouter" reads OpenRouter's rich format
	URL        string // the model list, ".../models"

	// CatalogSource is the provider's catalog_source (openrouter,
	// openai_compatible or price_card); "" is read from Type.
	CatalogSource string
	// UpstreamSlug names the upstream behind an openai_compatible or
	// price_card source, its endpoints' upstream_provider.
	UpstreamSlug string
	// Parse reads the model list an openai_compatible source serves at URL.
	Parse func(body []byte) (map[string]*Model, error)
	// Load reads a source with no list to fetch (a price card) as of now:
	// at boot, and again on every refresh, so its age is re-judged.
	Load func(now time.Time) (*Snapshot, error)
	// Bundled reads the snapshot a list source starts from at boot, until
	// its first fetch; nil when the gateway bundles none.
	Bundled func() (*Snapshot, error)
}

// Kind is the catalog source the provider reads as: its catalog_source, else
// openrouter for an openrouter type, else openai_compatible.
func (src Source) Kind() string {
	switch {
	case src.CatalogSource != "":
		return src.CatalogSource
	case src.Type == SourceOpenRouter:
		return SourceOpenRouter
	default:
		return SourceOpenAICompatible
	}
}

// apiBase is the URL the source's other catalog routes hang off (the model
// list's URL without "/models"), or "" when its URL has another shape.
func (src Source) apiBase() string {
	if base, ok := strings.CutSuffix(strings.TrimRight(src.URL, "/"), "/models"); ok {
		return base
	}
	return ""
}

const (
	// maxCatalogBytes bounds a catalog response; OpenRouter's is under 1 MB.
	maxCatalogBytes = 32 << 20
	// endpointsTTL is how long a model's endpoints are served before a
	// detail lookup re-reads them.
	endpointsTTL = 30 * time.Minute
	// crawlModels is how many models the endpoint crawler keeps current.
	crawlModels = 50
	// adminRefreshEvery is how often an admin may refresh one provider.
	adminRefreshEvery = time.Minute
)

var catalogHTTPClient = &http.Client{Timeout: 30 * time.Second}

// OptionsFromConfig is the store's configuration: catalog.*, the refresh
// interval, and each source's effective upstream fee. A provider whose type
// is openrouter counts as catalog_source openrouter when it names none.
func OptionsFromConfig(cfg *config.Config, sources []Source) Options {
	o := Options{
		HistorySize:      cfg.Catalog.HistorySize,
		KeepSnapshots:    cfg.Catalog.KeepSnapshots,
		SnapshotDir:      cfg.Catalog.SnapshotDir,
		BlockedModels:    cfg.Catalog.BlockedModels,
		RefreshInterval:  cfg.Managed.CatalogRefresh,
		EndpointsRefresh: cfg.Catalog.EndpointsRefresh,
		EndpointsRPS:     cfg.Catalog.EndpointsRPS,
		UpstreamFeeBps:   make(map[string]int, len(sources)),
	}
	for _, src := range sources {
		o.Providers = append(o.Providers, src.ProviderID)
		pc := cfg.Providers[src.ProviderID]
		if pc.CatalogSource == "" {
			pc.CatalogSource = src.Kind()
		}
		o.UpstreamFeeBps[src.ProviderID] = pc.EffectiveUpstreamFeeBps()
	}
	return o
}

// Seed puts each OpenRouter source's bundled snapshot, with the bundled
// endpoints, into the store, so routing and pricing work before the first
// live fetch. Its fetch time is the bundle's, so its age is honest. Other
// sources are left to SeedStatic and the first refresh.
func Seed(store *Store, sources []Source) {
	for _, src := range sources {
		if src.Kind() != SourceOpenRouter {
			continue
		}
		models, fetchedAt, err := BundledOpenRouter()
		if err != nil {
			slog.Error("catalog: bundled openrouter snapshot unreadable", "error", err)
			continue
		}
		snap := &Snapshot{ProviderID: src.ProviderID, Source: OriginBundled, CatalogSource: SourceOpenRouter,
			FetchedAt: fetchedAt, Models: models}
		if eps, err := BundledEndpoints(); err != nil {
			slog.Error("catalog: bundled openrouter endpoints unreadable", "error", err)
		} else {
			snap.ZDR = eps.ZDR
			for id, m := range models {
				if bundled, ok := eps.ByModel[id]; ok {
					m.Endpoints, m.EndpointsFetchedAt, m.EndpointsOrigin = bundled, eps.FetchedAt, OriginBundled
				}
			}
		}
		store.Put(snap)
		slog.Info("catalog: seeded from bundled snapshot", "provider", src.ProviderID, "models", len(models),
			"fetched_at", fetchedAt)
	}
}

// SeedStatic puts into the store each source that has no list to fetch (a
// price card), read as of the store's clock, and each list source's bundled
// snapshot. A source that cannot be read is left out and logged;
// sources.Resolve has already read each card once, so at boot this fails
// only when its file changed since. Seeding is not a refresh: it adds no
// history entry. What it may replace is seedOver's rule.
func SeedStatic(store *Store, sources []Source) {
	for _, src := range sources {
		kind := src.Kind()
		var snap *Snapshot
		var err error
		switch {
		case src.Load != nil:
			snap, err = src.Load(store.Now())
		case src.Bundled != nil:
			snap, err = src.Bundled()
		case kind == SourceOpenRouter:
			continue // Seed's
		}
		if err != nil {
			slog.Error("catalog: source unreadable at boot", "provider", src.ProviderID, "source", kind, "error", err)
			continue
		}
		static := src.Load != nil
		var seeded int
		if store.update(src.ProviderID, OriginBundled, func(prev *Snapshot) *Snapshot {
			next := seedOver(prev, snap, kind, static)
			if next != nil {
				seeded = len(next.Models)
			}
			return next
		}) {
			slog.Info("catalog: seeded", "provider", src.ProviderID, "source", kind, "models", seeded)
		}
	}
}

// seedOver is what a source of kind seeds over prev, its current snapshot, or
// nil to keep prev. A card (static: seed is what Load read) always replaces
// it. A bundled list replaces it unless prev is a list of the same kind
// fetched later (one Configure restored from catalog.snapshot_dir), so a boot
// never rolls the catalog back. A list of another kind never stays: Seed put
// OpenRouter's bundle under any openrouter-type provider before
// sources.Resolve named its catalog_source, and a restored snapshot may
// predate a catalog_source change. With nothing bundled (seed nil) it becomes
// an empty list of the source's kind, which the first fetch fills.
func seedOver(prev, seed *Snapshot, kind string, static bool) *Snapshot {
	sameKind := prev != nil && prev.CatalogSource == kind
	switch {
	case static:
		return seed
	case seed != nil && !(sameKind && prev.FetchedAt.After(seed.FetchedAt)):
		return seed
	case seed == nil && prev != nil && !sameKind:
		return &Snapshot{Source: OriginBundled, CatalogSource: kind, Models: map[string]*Model{}}
	}
	return nil
}

// Now is the store's clock (Options.Now, else time.Now).
func (s *Store) Now() time.Time {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.now()
}

// Refresher keeps the store's catalogs current: the model lists (with the
// ZDR list), the endpoints of the newest fixed-price models (crawlSelection),
// and an endpoint detail read on demand. Admin refreshes reach it through the
// store.
type Refresher struct {
	ctx      context.Context
	store    *Store
	sources  map[string]Source
	order    []string
	onUpdate func()
	client   *http.Client
	pace     *pacer
	// listed closes once Run has refreshed the model lists for the first
	// time (or will not), so the first crawl picks from the live lists.
	listed     chan struct{}
	listedOnce sync.Once
}

// NewRefresher returns a refresher of sources into store, registered with
// the store for admin refreshes and endpoint reads. onUpdate runs after a
// refresh that changed a model list. Its work stops when ctx ends.
func NewRefresher(ctx context.Context, store *Store, sources []Source, onUpdate func()) *Refresher {
	r := &Refresher{ctx: ctx, store: store, sources: map[string]Source{}, onUpdate: onUpdate, client: catalogHTTPClient,
		listed: make(chan struct{})}
	for _, src := range sources {
		r.sources[src.ProviderID] = src
		r.order = append(r.order, src.ProviderID)
	}
	sort.Strings(r.order)
	store.mu.Lock()
	r.pace = newPacer(store.opts.EndpointsRPS, store.now, sleepCtx)
	store.refresher = r
	store.mu.Unlock()
	return r
}

// Run refreshes every model list every interval until the refresher's
// context ends, starting immediately. States are re-derived on every tick,
// so a model whose expiration date comes near turns deprecated.
func (r *Refresher) Run(interval time.Duration) {
	defer r.markListed()
	if len(r.order) == 0 || interval <= 0 {
		return
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		r.RefreshModels(r.order...)
		r.markListed()
		r.store.RefreshStates()
		select {
		case <-r.ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// RefreshModels fetches the providers' model lists (and, for an OpenRouter
// source, its ZDR list) once, and re-reads each static source. A provider
// whose fetch or read fails keeps its previous snapshot. onUpdate runs once
// if any version changed.
func (r *Refresher) RefreshModels(providerIDs ...string) {
	changed := false
	for _, id := range providerIDs {
		src, ok := r.sources[id]
		if !ok {
			continue
		}
		if src.Load != nil {
			changed = r.reload(src) || changed
			continue
		}
		models, err := r.fetchModels(src)
		if err != nil {
			slog.Warn("catalog: refresh failed, keeping previous snapshot", "provider", id, "error", err)
			continue
		}
		var zdr map[string]bool
		if src.Kind() == SourceOpenRouter && src.apiBase() != "" {
			if zdr, err = r.fetchZDR(src); err != nil {
				slog.Warn("catalog: zdr list unreadable, keeping the previous one", "provider", id, "error", err)
			}
		}
		now := r.store.now()
		if r.store.update(id, OriginLive, func(prev *Snapshot) *Snapshot {
			return liveSnapshot(src, prev, models, zdr, now)
		}) {
			changed = true
		}
		slog.Info("catalog: refreshed", "provider", id, "models", len(models), "snapshot_id", r.store.Load().ID())
	}
	if changed && r.onUpdate != nil {
		r.onUpdate()
	}
}

// reload re-reads a static source as of now, reporting whether the catalog
// changed (its age crossed the stale line, or its file was edited).
func (r *Refresher) reload(src Source) bool {
	snap, err := src.Load(r.store.Now())
	if err != nil {
		slog.Warn("catalog: re-read failed, keeping previous snapshot", "provider", src.ProviderID, "error", err)
		return false
	}
	changed := r.store.update(src.ProviderID, OriginPriceCard, func(*Snapshot) *Snapshot { return snap })
	slog.Info("catalog: re-read", "provider", src.ProviderID, "source", src.Kind(), "models", len(snap.Models),
		"snapshot_id", r.store.Load().ID())
	return changed
}

// liveSnapshot is a freshly fetched model list: a model still listed keeps
// the endpoints it had until they are read again, and a ZDR list that could
// not be read is the previous one.
func liveSnapshot(src Source, prev *Snapshot, models map[string]*Model, zdr map[string]bool, now time.Time) *Snapshot {
	next := &Snapshot{ProviderID: src.ProviderID, Source: OriginLive, CatalogSource: src.Kind(), FetchedAt: now,
		Models: models, ZDR: zdr}
	if prev == nil {
		return next
	}
	if next.ZDR == nil {
		next.ZDR = prev.ZDR
	}
	for id, m := range models {
		if pm, ok := prev.Models[id]; ok && len(m.Endpoints) == 0 {
			m.Endpoints, m.EndpointsFetchedAt, m.EndpointsOrigin = pm.Endpoints, pm.EndpointsFetchedAt, pm.EndpointsOrigin
		}
	}
	return next
}

// RequestRefresh starts a model-list refresh of providerIDs (every source
// when none is named), each at most once a minute. It returns the providers
// it started, and when it started none, how long until one may run.
func (s *Store) RequestRefresh(providerIDs []string) (started []string, retryAfter time.Duration, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r := s.refresher
	if r == nil {
		return nil, 0, errNoRefresher
	}
	if len(providerIDs) == 0 {
		providerIDs = r.order
	}
	now := s.now()
	for _, id := range providerIDs {
		if _, ok := r.sources[id]; !ok {
			return nil, 0, fmt.Errorf("%w: %q", ErrUnknownProvider, id)
		}
		if last, ok := s.lastAdminRefresh[id]; ok && now.Sub(last) < adminRefreshEvery {
			if wait := adminRefreshEvery - now.Sub(last); retryAfter == 0 || wait < retryAfter {
				retryAfter = wait
			}
			continue
		}
		s.lastAdminRefresh[id] = now
		started = append(started, id)
	}
	if len(started) > 0 {
		retryAfter = 0
		go r.RefreshModels(started...)
	}
	return started, retryAfter, nil
}

// ErrUnknownProvider is a refresh or lookup naming no catalog provider.
var ErrUnknownProvider = errors.New("catalog: unknown provider")

var errNoRefresher = errors.New("catalog: no refresher is running")

// ModelEndpoints returns model as listed by providerID (the first provider
// listing it when ""), re-reading its endpoints from the upstream first
// unless a live read is younger than endpointsTTL (bundled endpoints are
// always re-read). A failed read serves what the catalog has.
func (s *Store) ModelEndpoints(ctx context.Context, providerID, model string) (string, *Model, bool) {
	cat := s.Load()
	var m *Model
	var ok bool
	if providerID == "" {
		providerID, m, ok = cat.Find(model)
	} else {
		m, ok = cat.Get(providerID, model)
	}
	if !ok {
		return "", nil, false
	}
	s.mu.Lock()
	r, now := s.refresher, s.now()
	s.mu.Unlock()
	if r == nil || m.EndpointsOrigin == OriginLive && now.Sub(m.EndpointsFetchedAt) < endpointsTTL {
		return providerID, m, true
	}
	if err := r.refreshEndpoints(ctx, providerID, []string{model}); err != nil {
		slog.Warn("catalog: endpoint read failed, serving the catalog's", "provider", providerID, "model", model, "error", err)
	}
	if fresh, ok := s.Load().Get(providerID, model); ok {
		m = fresh
	}
	return providerID, m, true
}

// markListed records that the first model-list refresh is done.
func (r *Refresher) markListed() { r.listedOnce.Do(func() { close(r.listed) }) }

// RunEndpoints re-reads the endpoints of crawlSelection's models every
// catalog.endpoints_refresh until the refresher's context ends, the first
// time once Run's first model-list refresh is done (so it crawls the live
// lists, not the bundled ones they replace). A zero interval turns it off.
func (r *Refresher) RunEndpoints() {
	r.store.mu.Lock()
	interval := r.store.opts.EndpointsRefresh
	r.store.mu.Unlock()
	if interval <= 0 || len(r.order) == 0 {
		return
	}
	select {
	case <-r.ctx.Done():
		return
	case <-r.listed:
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		r.Crawl()
		select {
		case <-r.ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// Crawl re-reads the endpoints of crawlModels models once, at most
// catalog.endpoints_rps requests a second, applying each provider's as one
// catalog change.
func (r *Refresher) Crawl() {
	for _, id := range r.order {
		if models := crawlSelection(r.store.Load(), id, crawlModels); len(models) > 0 {
			if err := r.refreshEndpoints(r.ctx, id, models); err != nil {
				slog.Warn("catalog: endpoint crawl incomplete", "provider", id, "error", err)
			}
		}
	}
}

// crawlSelection is the models whose endpoints the crawler keeps current:
// the provider's newest fixed-price text models that are not variants
// (":free", ":batch") and not blocked or deprecated, the shape of the
// default auto pool.
func crawlSelection(cat *Catalog, providerID string, n int) []string {
	snap := cat.byProvider[providerID]
	if snap == nil {
		return nil
	}
	var pool []*Model
	for _, m := range snap.Models {
		if m.Priced() && !strings.Contains(m.ID, ":") && m.Produces("text") &&
			m.State != StateBlocked && m.State != StateDeprecated {
			pool = append(pool, m)
		}
	}
	sort.Slice(pool, func(i, j int) bool {
		if pool[i].Created != pool[j].Created {
			return pool[i].Created > pool[j].Created
		}
		return pool[i].ID < pool[j].ID
	})
	if len(pool) > n {
		pool = pool[:n]
	}
	out := make([]string, len(pool))
	for i, m := range pool {
		out[i] = m.ID
	}
	return out
}

// refreshEndpoints reads models' endpoints from providerID's source, paced,
// and applies every one read as one catalog change. A model whose read
// fails keeps its endpoints.
func (r *Refresher) refreshEndpoints(ctx context.Context, providerID string, models []string) error {
	src, ok := r.sources[providerID]
	if !ok || src.Kind() != SourceOpenRouter || src.apiBase() == "" {
		return nil
	}
	read := map[string][]Endpoint{}
	var errs []error
	for _, model := range models {
		if err := r.pace.wait(ctx); err != nil {
			errs = append(errs, err)
			break
		}
		eps, err := r.fetchEndpoints(ctx, src, model)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		read[model] = eps
	}
	if len(read) > 0 {
		now := r.store.now()
		r.store.update(providerID, originEndpoints, func(prev *Snapshot) *Snapshot {
			return withEndpoints(prev, read, now)
		})
	}
	return errors.Join(errs...)
}

// withEndpoints is prev with the endpoints read replacing those of the
// models it still lists.
func withEndpoints(prev *Snapshot, read map[string][]Endpoint, now time.Time) *Snapshot {
	if prev == nil {
		return nil
	}
	next := *prev
	next.Models = make(map[string]*Model, len(prev.Models))
	for id, m := range prev.Models {
		if eps, ok := read[id]; ok {
			mc := *m
			mc.Endpoints, mc.EndpointsFetchedAt, mc.EndpointsOrigin = eps, now, OriginLive
			m = &mc
		}
		next.Models[id] = m
	}
	return &next
}

func (r *Refresher) fetchModels(src Source) (map[string]*Model, error) {
	if src.Kind() != SourceOpenRouter && src.Parse == nil {
		return nil, fmt.Errorf("catalog source %s has no reader (sources.Resolve attaches one)", src.Kind())
	}
	body, err := r.get(r.ctx, src.URL)
	if err != nil {
		return nil, err
	}
	if src.Kind() == SourceOpenRouter {
		return ParseOpenRouter(body)
	}
	return src.Parse(body)
}

func (r *Refresher) fetchZDR(src Source) (map[string]bool, error) {
	body, err := r.get(r.ctx, src.apiBase()+"/endpoints/zdr")
	if err != nil {
		return nil, err
	}
	return ParseZDR(body)
}

func (r *Refresher) fetchEndpoints(ctx context.Context, src Source, model string) ([]Endpoint, error) {
	parts := strings.Split(model, "/")
	for i, p := range parts {
		parts[i] = url.PathEscape(p)
	}
	body, err := r.get(ctx, src.apiBase()+"/models/"+strings.Join(parts, "/")+"/endpoints")
	if err != nil {
		return nil, err
	}
	return ParseOpenRouterEndpoints(model, body)
}

func (r *Refresher) get(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	resp, err := r.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET %s: HTTP %d", u, resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxCatalogBytes))
}

// pacer spaces requests at most rps a second on a clock tests can fake.
type pacer struct {
	mu       sync.Mutex
	interval time.Duration
	next     time.Time
	now      func() time.Time
	sleep    func(context.Context, time.Duration) error
}

func newPacer(rps float64, now func() time.Time, sleep func(context.Context, time.Duration) error) *pacer {
	return &pacer{interval: time.Duration(float64(time.Second) / rps), now: now, sleep: sleep}
}

// wait blocks until the next request may start, or ctx ends.
func (p *pacer) wait(ctx context.Context) error {
	p.mu.Lock()
	now := p.now()
	at := p.next
	if at.Before(now) {
		at = now
	}
	p.next = at.Add(p.interval)
	p.mu.Unlock()
	if d := at.Sub(now); d > 0 {
		return p.sleep(ctx, d)
	}
	return ctx.Err()
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
