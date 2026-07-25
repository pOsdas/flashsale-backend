package wildberries

type wbHTTPError struct {
	StatusCode int
	Body       string
}

type wbProductsResponse struct {
	Products []wbProduct `json:"products"`
	Data     wbData      `json:"data"`
}

func (r wbProductsResponse) productList() []wbProduct {
	if len(r.Products) > 0 {
		return r.Products
	}

	return r.Data.Products
}

type wbData struct {
	Products []wbProduct `json:"products"`
}

type wbProduct struct {
	ID            int64    `json:"id"`
	Brand         string   `json:"brand"`
	Supplier      string   `json:"supplier"`
	ReviewRating  float64  `json:"reviewRating"`
	Feedbacks     int      `json:"feedbacks"`
	Name          string   `json:"name"`
	PriceU        int      `json:"priceU"`
	SalePriceU    int      `json:"salePriceU"`
	TotalQuantity int      `json:"totalQuantity"`
	Sizes         []wbSize `json:"sizes"`
}

type wbSize struct {
	Price wbPrice `json:"price"`
}

type wbPrice struct {
	Basic   int `json:"basic"`
	Product int `json:"product"`
	Total   int `json:"total"`
}
