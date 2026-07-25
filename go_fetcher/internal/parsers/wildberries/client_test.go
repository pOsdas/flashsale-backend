package wildberries

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDoJSONRequestUsesBrowserFallbackOnForbidden(t *testing.T) {
	browserServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != wbBrowserFetchPath {
			http.NotFound(w, r)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status_code": http.StatusOK,
			"body":        map[string]any{"value": "from-browser"},
		})
	}))
	defer browserServer.Close()

	httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte("<html><center>Angie</center></html>"))
	}))
	defer httpServer.Close()

	parser := NewParser(ParserConfig{
		BrowserFetcherEnabled: true,
		BrowserFetcherURL:     browserServer.URL,
		BrowserFetcherTimeout: time.Second,
	}, nil)
	parser.client = httpServer.Client()

	var target struct {
		Value string `json:"value"`
	}
	if err := parser.doJSONRequest(context.Background(), httpServer.URL, &target); err != nil {
		t.Fatalf("doJSONRequest returned error: %v", err)
	}
	if target.Value != "from-browser" {
		t.Fatalf("Value = %q, want from-browser", target.Value)
	}
}

func TestSetWBHeadersUsesConsistentEdgeProfile(t *testing.T) {
	req, err := http.NewRequest(http.MethodGet, buildDetailURL("302421341"), nil)
	if err != nil {
		t.Fatal(err)
	}

	setWBHeaders(req, "session=value")

	if !strings.Contains(req.Header.Get("User-Agent"), "Edg/148.") {
		t.Fatalf("User-Agent does not identify Edge: %q", req.Header.Get("User-Agent"))
	}

	clientHints := req.Header.Get("sec-ch-ua")
	if !strings.Contains(clientHints, `"Microsoft Edge";v="148"`) {
		t.Fatalf("Client Hints do not identify the same Edge version: %q", clientHints)
	}

	if got := req.Header.Get("Cookie"); got != "session=value" {
		t.Fatalf("Cookie = %q, want %q", got, "session=value")
	}
}

func TestSetWBHeadersUsesProductReferer(t *testing.T) {
	req, err := http.NewRequest(http.MethodGet, buildDetailURL("302421341"), nil)
	if err != nil {
		t.Fatal(err)
	}

	setWBHeaders(req, "")

	want := "https://www.wildberries.ru/catalog/302421341/detail.aspx"
	if got := req.Header.Get("Referer"); got != want {
		t.Fatalf("Referer = %q, want %q", got, want)
	}
}

func TestSetWBHeadersPreservesActualSearchQueryInReferer(t *testing.T) {
	req, err := http.NewRequest(http.MethodGet, buildSearchURL("iphone", 1), nil)
	if err != nil {
		t.Fatal(err)
	}

	setWBHeaders(req, "")

	want := "https://www.wildberries.ru/catalog/0/search.aspx?search=iphone"
	if got := req.Header.Get("Referer"); got != want {
		t.Fatalf("Referer = %q, want %q", got, want)
	}
}

func TestSetWBHeadersUsesNeutralRefererForCategory(t *testing.T) {
	req, err := http.NewRequest(http.MethodGet, buildCategoryURL("кошельки", 1), nil)
	if err != nil {
		t.Fatal(err)
	}

	setWBHeaders(req, "")

	want := "https://www.wildberries.ru/"
	if got := req.Header.Get("Referer"); got != want {
		t.Fatalf("Referer = %q, want %q", got, want)
	}
}
