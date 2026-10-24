package capability

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The test capability echo: a vendor-neutral
// stateless capability whose vendors return the request body, so the
// selector, fallback, pricing and ledger can be proven end to end against a
// mock vendor. It is registered only when the operator config sets
// tools.enabled and carries a capabilities.echo entry; no shipped config does.
const (
	EchoCapability = "echo"
	// EchoEndpointType is the endpoint type echo calls are logged under: a
	// stateless call of search's fallback class, and a value log-entry-v2's
	// endpoint_type enum accepts.
	EchoEndpointType = "search"
	// echoPath is where an echo vendor answers, under its base_url.
	echoPath = "/echo"
	// maxEchoBody bounds what an echo vendor may return.
	maxEchoBody = 1 << 20
)

// defaultEchoVendors are the capability mock's two echo fakes.
var defaultEchoVendors = []string{"echo-a", "echo-b"}

// RegisterConfigured registers the capabilities cfg configures: only
// echo, when tools.enabled is set and capabilities.echo exists. Its vendors
// are capabilities.echo.defaults.vendors, else echo-a and echo-b.
func RegisterConfigured(reg *Registry, cfg *config.Config) error {
	if cfg == nil || !cfg.Tools.Enabled {
		return nil
	}
	echo, ok := cfg.Capabilities.PerCapability[EchoCapability]
	if !ok {
		return nil
	}
	if err := reg.RegisterCapability(EchoCapability, Spec{Class: ClassStatelessCrossVendor, EndpointType: EchoEndpointType}); err != nil {
		return err
	}
	vendors := defaultEchoVendors
	if names := pinnedProviders(map[string]interface{}{"provider": echo.Defaults["vendors"]}); len(names) > 0 {
		vendors = names
	}
	for _, v := range vendors {
		if err := reg.Register(EchoCapability, v, EchoAdapter{Client: http.DefaultClient}); err != nil {
			return err
		}
	}
	return nil
}

// EchoAdapter calls an echo vendor: POST <base_url>/echo with the request
// body. A 2xx body is the result; its optional usage.units and
// usage.cost_usd numbers are what the vendor reports.
type EchoAdapter struct {
	Client *http.Client
}

// Call implements Adapter.
func (e EchoAdapter) Call(ctx context.Context, call VendorCall) (VendorResponse, error) {
	if call.Credential.BaseURL == "" {
		return VendorResponse{}, &VendorError{Message: "echo vendor " + call.Vendor + " has no base_url"}
	}
	url := strings.TrimRight(call.Credential.BaseURL, "/") + echoPath
	body := call.Request.Body
	if len(body) == 0 {
		body = json.RawMessage(`{}`)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return VendorResponse{}, &VendorError{Message: err.Error()}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+call.Credential.APIKey)
	resp, err := e.Client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return VendorResponse{}, ctx.Err()
		}
		return VendorResponse{}, &VendorError{Message: err.Error()}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxEchoBody))
	if err != nil {
		return VendorResponse{}, &VendorError{Message: err.Error()}
	}
	if resp.StatusCode/100 != 2 {
		return VendorResponse{}, &VendorError{Status: resp.StatusCode, Message: snippet(raw)}
	}
	return echoResponse(call.SKU.SKU, raw)
}

// echoResponse reads what the vendor reported from its body's optional
// usage object, as the capability mock's echo fakes report it:
// usage.units and usage.cost_usd.
func echoResponse(sku string, raw []byte) (VendorResponse, error) {
	var reported struct {
		Usage struct {
			Units   *float64 `json:"units"`
			CostUSD *float64 `json:"cost_usd"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &reported); err != nil {
		return VendorResponse{}, &VendorError{Status: http.StatusBadGateway, Message: fmt.Sprintf("echo vendor answered non-JSON: %s", snippet(raw))}
	}
	out := VendorResponse{Body: json.RawMessage(raw), CostUSD: reported.Usage.CostUSD}
	if u := reported.Usage.Units; u != nil {
		out.Units = []Unit{{SKU: sku, Count: *u}}
	}
	return out, nil
}

func snippet(b []byte) string {
	const n = 256
	if len(b) > n {
		b = b[:n]
	}
	return string(b)
}
