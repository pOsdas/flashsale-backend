package wildberries

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const maxWBErrorBodyPreviewLength = 500

const (
	wbBrowserMajorVersion = "148"
	wbBrowserUserAgent    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
		"(KHTML, like Gecko) Chrome/" + wbBrowserMajorVersion + ".0.0.0 Safari/537.36 " +
		"Edg/" + wbBrowserMajorVersion + ".0.0.0"
	wbBrowserClientHints = `"Not_A Brand";v="99", "Chromium";v="148", "Microsoft Edge";v="148"`
)

type wbTemporaryBlockedError struct {
	Reason       string
	BlockedUntil time.Time
}

func (e *wbTemporaryBlockedError) Error() string {
	return fmt.Sprintf(
		"wildberries parser is temporarily blocked: reason=%s, blocked_until=%s",
		e.Reason,
		e.BlockedUntil.Format(time.RFC3339),
	)
}

func (p *Parser) doJSONRequest(ctx context.Context, requestURL string, target any) error {
	if blocked, until, reason := p.isTemporarilyBlocked(); blocked {
		return &wbTemporaryBlockedError{
			Reason:       reason,
			BlockedUntil: until,
		}
	}

	var lastErr error

	for attempt := 0; attempt <= p.maxRetries; attempt++ {
		if attempt > 0 {
			delay := p.retryDelay(attempt)

			p.logger.Warn(
				"wildberries request retry",
				slog.Int("attempt", attempt),
				slog.Duration("delay", delay),
				slog.String("url", requestURL),
				slog.String("error", lastErr.Error()),
			)

			if err := sleepWithContext(ctx, delay); err != nil {
				return err
			}
		} else if p.requestDelay > 0 {
			if err := sleepWithContext(ctx, p.requestDelay); err != nil {
				return err
			}
		}

		err := p.doJSONRequestOnce(ctx, requestURL, target)
		if err == nil {
			return nil
		}

		lastErr = err

		if isWBRateLimitedError(err) {
			p.blockTemporarily("rate_limited", 10*time.Minute)
			return err
		}

		if isWBBlockedByAntibotError(err) {
			p.logger.Warn(
				"wildberries antibot response received, forcing cookie reload",
				slog.String("url", requestURL),
				slog.String("cookie_source", p.cookieSource()),
				slog.Bool("cookie_present", p.hasCookie()),
			)

			p.forceReloadCookie()

			if p.browserClient != nil && p.browserClient.Enabled() {
				p.logger.Warn(
					"wildberries browser fallback started",
					slog.String("url", requestURL),
				)

				if browserErr := p.browserClient.FetchJSON(ctx, requestURL, target); browserErr == nil {
					p.logger.Info("wildberries browser fallback succeeded", slog.String("url", requestURL))
					return nil
				} else {
					p.logger.Error(
						"wildberries browser fallback failed",
						slog.String("url", requestURL),
						slog.String("error", browserErr.Error()),
					)
					p.blockTemporarily("blocked_by_antibot", 15*time.Minute)
					return fmt.Errorf("WB HTTP request blocked: %w; browser fallback failed: %v", err, browserErr)
				}
			}

			p.blockTemporarily("blocked_by_antibot", 15*time.Minute)
			return err
		}

		if !isRetryableWBError(err) {
			return err
		}
	}

	return fmt.Errorf("request failed after %d retries: %w", p.maxRetries, lastErr)
}

func (p *Parser) doJSONRequestOnce(ctx context.Context, requestURL string, target any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	setWBHeaders(req, p.currentCookie())

	resp, err := p.client.Do(req)
	if err != nil {
		return fmt.Errorf("execute request: %w", err)
	}
	defer resp.Body.Close()

	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read WB response body: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &wbHTTPError{
			StatusCode: resp.StatusCode,
			Body:       string(responseBody),
		}
	}

	if err := json.Unmarshal(responseBody, target); err != nil {
		return fmt.Errorf("decode response: %w, body: %s", err, truncateWBBodyPreview(string(responseBody)))
	}

	return nil
}

func (p *Parser) retryDelay(attempt int) time.Duration {
	multiplier := 1 << (attempt - 1)
	return time.Duration(multiplier) * p.retryBaseDelay
}

func setWBHeaders(req *http.Request, cookie string) {
	req.Header.Set("Accept", "application/json, text/plain, */*")
	req.Header.Set("Accept-Language", "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7")
	req.Header.Set("Connection", "keep-alive")
	req.Header.Set("User-Agent", wbBrowserUserAgent)
	req.Header.Set("sec-ch-ua", wbBrowserClientHints)
	req.Header.Set("sec-ch-ua-mobile", "?0")
	req.Header.Set("sec-ch-ua-platform", `"Windows"`)

	host := req.URL.Hostname()

	switch host {
	case "search.wb.ru":
		setWBSearchHeaders(req)

	case "www.wildberries.ru", "wildberries.ru":
		setWBInternalHeaders(req)

	default:
		setWBDefaultHeaders(req)
	}

	if strings.TrimSpace(cookie) != "" {
		req.Header.Set("Cookie", cookie)
	}
}

func setWBSearchHeaders(req *http.Request) {
	req.Header.Set("Origin", "https://www.wildberries.ru")
	req.Header.Set("Referer", "https://www.wildberries.ru/catalog/0/search.aspx?search=iphone")
	req.Header.Set("Sec-Fetch-Dest", "empty")
	req.Header.Set("Sec-Fetch-Mode", "cors")
	req.Header.Set("Sec-Fetch-Site", "cross-site")
}

func setWBInternalHeaders(req *http.Request) {
	req.Header.Set("Referer", wbInternalReferer(req.URL))
	req.Header.Set("Sec-Fetch-Dest", "empty")
	req.Header.Set("Sec-Fetch-Mode", "cors")
	req.Header.Set("Sec-Fetch-Site", "same-origin")
}

func wbInternalReferer(requestURL *url.URL) string {
	if requestURL == nil {
		return "https://www.wildberries.ru/"
	}

	query := requestURL.Query()

	if strings.Contains(requestURL.Path, "/card/") || strings.Contains(requestURL.Path, "/u-card/") {
		if nmID := strings.TrimSpace(query.Get("nm")); nmID != "" {
			return "https://www.wildberries.ru/catalog/" + url.PathEscape(nmID) + "/detail.aspx"
		}
	}

	if strings.Contains(requestURL.Path, "/search/") {
		searchQuery := strings.TrimSpace(query.Get("query"))
		if searchQuery != "" && !strings.HasPrefix(searchQuery, "menu_redirect_subject_v2_") {
			values := url.Values{}
			values.Set("search", searchQuery)
			return "https://www.wildberries.ru/catalog/0/search.aspx?" + values.Encode()
		}
	}

	return "https://www.wildberries.ru/"
}

func setWBDefaultHeaders(req *http.Request) {
	req.Header.Set("Referer", "https://www.wildberries.ru/")
	req.Header.Set("Sec-Fetch-Dest", "empty")
	req.Header.Set("Sec-Fetch-Mode", "cors")
	req.Header.Set("Sec-Fetch-Site", "same-origin")
}

func sleepWithContext(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func isRetryableWBError(err error) bool {
	httpErr, ok := err.(*wbHTTPError)
	if !ok {
		return false
	}

	return httpErr.StatusCode >= 500
}

func isWBRateLimitedError(err error) bool {
	httpErr, ok := err.(*wbHTTPError)
	if !ok {
		return false
	}

	return httpErr.StatusCode == http.StatusTooManyRequests
}

func isWBBlockedByAntibotError(err error) bool {
	httpErr, ok := err.(*wbHTTPError)
	if !ok {
		return false
	}

	body := strings.ToLower(httpErr.Body)

	return httpErr.StatusCode == http.StatusForbidden ||
		httpErr.StatusCode == 498 ||
		strings.Contains(body, "__wbaas/challenges/antibot") ||
		strings.Contains(body, "почти готово") ||
		strings.Contains(body, "antibot")
}

func (e *wbHTTPError) Error() string {
	return fmt.Sprintf(
		"unexpected WB status code: %d, body: %s",
		e.StatusCode,
		truncateWBBodyPreview(e.Body),
	)
}

func truncateWBBodyPreview(value string) string {
	value = strings.TrimSpace(value)

	if len(value) <= maxWBErrorBodyPreviewLength {
		return value
	}

	return value[:maxWBErrorBodyPreviewLength] + "...[truncated]"
}
