package browsergateway

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// TemporaryUnavailableError means that the VPN/browser gateway is healthy
// enough to answer, but cannot accept marketplace work at this moment.
// Callers must retry later without marking the monitored product as failed.
type TemporaryUnavailableError struct {
	StatusCode        int
	Message           string
	RetryAfterSeconds int
}

func (e *TemporaryUnavailableError) Error() string {
	message := strings.TrimSpace(e.Message)
	if message == "" {
		message = "marketplace gateway is temporarily unavailable"
	}

	if e.RetryAfterSeconds > 0 {
		return fmt.Sprintf(
			"%s (status=%d, retry_after_seconds=%d)",
			message,
			e.StatusCode,
			e.RetryAfterSeconds,
		)
	}

	return fmt.Sprintf("%s (status=%d)", message, e.StatusCode)
}

func ParseRetryAfter(rawValue string) int {
	value, err := strconv.Atoi(strings.TrimSpace(rawValue))
	if err != nil || value < 0 {
		return 0
	}
	return value
}

func TemporaryErrorFromHTTPResponse(
	statusCode int,
	body []byte,
	retryAfterHeader string,
) error {
	if statusCode != 425 && statusCode != 429 && statusCode != 503 {
		return nil
	}

	var payload struct {
		Error           string `json:"error"`
		FailureCategory string `json:"failure_category"`
	}
	_ = json.Unmarshal(body, &payload)

	// A 429 from the actual marketplace can be returned through the VPN
	// gateway with X-VPN headers and must be handled by the orchestrator.
	// The gateway's own busy/not-ready responses carry this category.
	if statusCode == 429 && payload.FailureCategory != "gateway_unavailable" {
		return nil
	}

	return &TemporaryUnavailableError{
		StatusCode:        statusCode,
		Message:           strings.TrimSpace(payload.Error),
		RetryAfterSeconds: ParseRetryAfter(retryAfterHeader),
	}
}
