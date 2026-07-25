package wildberries

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const wbBrowserFetchPath = "/api/v1/fetch"

type BrowserClient struct {
	client   *http.Client
	endpoint string
}

type wbBrowserFetchResponse struct {
	StatusCode int             `json:"status_code"`
	Body       json.RawMessage `json:"body"`
	Error      string          `json:"error"`
}

func NewBrowserClient(enabled bool, rawURL string, timeout time.Duration) *BrowserClient {
	if !enabled || strings.TrimSpace(rawURL) == "" {
		return nil
	}

	if timeout <= 0 {
		timeout = defaultWBBrowserFetcherTimeout
	}

	return &BrowserClient{
		client:   &http.Client{Timeout: timeout},
		endpoint: strings.TrimRight(strings.TrimSpace(rawURL), "/"),
	}
}

func (c *BrowserClient) Enabled() bool {
	return c != nil && c.client != nil && c.endpoint != ""
}

func (c *BrowserClient) FetchJSON(ctx context.Context, requestURL string, target any) error {
	if !c.Enabled() {
		return fmt.Errorf("WB browser fetcher is not configured")
	}

	payload, err := json.Marshal(map[string]string{"url": requestURL})
	if err != nil {
		return fmt.Errorf("encode browser fetcher request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint+wbBrowserFetchPath, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("create browser fetcher request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("execute browser fetcher request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read browser fetcher response: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("browser fetcher status %d: %s", resp.StatusCode, truncateWBBodyPreview(string(body)))
	}

	var result wbBrowserFetchResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("decode browser fetcher response: %w", err)
	}
	if result.Error != "" {
		return fmt.Errorf("browser fetcher error: %s", result.Error)
	}
	if result.StatusCode < 200 || result.StatusCode >= 300 {
		return fmt.Errorf("browser upstream status %d: %s", result.StatusCode, truncateWBBodyPreview(string(result.Body)))
	}
	if err := json.Unmarshal(result.Body, target); err != nil {
		return fmt.Errorf("decode browser upstream JSON: %w", err)
	}

	return nil
}
