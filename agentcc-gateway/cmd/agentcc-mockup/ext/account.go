package ext

import (
	"encoding/json"
	"math"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// accountHandler serves POST /__account {"balance_usd": <number or decimal
// string>}: it sets the account's remaining balance, which /api/v1/credits
// then reports as total_credits less total_usage. The charges are kept, and
// later calls draw the balance down. /__reset restores the fixtures' balance.
func accountHandler(state *mock.State) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			BalanceUSD json.Number `json:"balance_usd"`
		}
		err := json.NewDecoder(r.Body).Decode(&in)
		balance, perr := strconv.ParseFloat(in.BalanceUSD.String(), 64)
		if err != nil || perr != nil || balance < 0 || math.IsInf(balance, 0) {
			writeError(w, http.StatusBadRequest, `the body must be {"balance_usd": <a non-negative USD amount>}`)
			return
		}
		state.SetBalanceMicros(usdToMicros(balance))
		writeJSON(w, http.StatusOK, map[string]any{
			"balance_usd":   microsToUSD(state.BalanceMicros()),
			"total_credits": microsToUSD(state.CreditMicros()),
		})
	}
}
