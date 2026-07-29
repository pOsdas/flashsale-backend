package browsergateway

import (
	"errors"
	"testing"
)

func TestTemporaryErrorFromHTTPResponse(t *testing.T) {
	err := TemporaryErrorFromHTTPResponse(
		425,
		[]byte(`{"error":"parse window is not ready","failure_category":"gateway_unavailable"}`),
		"123",
	)

	var temporary *TemporaryUnavailableError
	if !errors.As(err, &temporary) {
		t.Fatalf("expected TemporaryUnavailableError, got %T", err)
	}
	if temporary.RetryAfterSeconds != 123 {
		t.Fatalf("expected retry-after 123, got %d", temporary.RetryAfterSeconds)
	}
}

func TestMarketplace429IsNotGatewayUnavailable(t *testing.T) {
	err := TemporaryErrorFromHTTPResponse(
		429,
		[]byte(`{"error":"marketplace rate limit"}`),
		"",
	)
	if err != nil {
		t.Fatalf("expected marketplace 429 to stay marketplace error, got %v", err)
	}
}
