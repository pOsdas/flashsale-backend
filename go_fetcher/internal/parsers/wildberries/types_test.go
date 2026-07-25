package wildberries

import (
	"encoding/json"
	"testing"
)

func TestWBProductsResponseProductListReadsRootProducts(t *testing.T) {
	var response wbProductsResponse

	if err := json.Unmarshal([]byte(`{"products":[{"id":123,"name":"root product"}]}`), &response); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}

	products := response.productList()
	if len(products) != 1 {
		t.Fatalf("expected 1 product, got %d", len(products))
	}

	if products[0].ID != 123 {
		t.Fatalf("expected root product id 123, got %d", products[0].ID)
	}
}

func TestWBProductsResponseProductListReadsDataProducts(t *testing.T) {
	var response wbProductsResponse

	if err := json.Unmarshal([]byte(`{"data":{"products":[{"id":456,"name":"data product"}]}}`), &response); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}

	products := response.productList()
	if len(products) != 1 {
		t.Fatalf("expected 1 product, got %d", len(products))
	}

	if products[0].ID != 456 {
		t.Fatalf("expected data product id 456, got %d", products[0].ID)
	}
}
