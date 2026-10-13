# List orders

Returns a page of orders for the authenticated account, newest first.

```
GET /v1/orders?limit=20&status=paid
```

## Query parameters

| Name | Type | Description |
|---|---|---|
| limit | integer | Number of orders to return, from 1 to 100 |
| status | string | Filter by paid, pending or refunded |
| cursor | string | Opaque value from the previous response |

The response contains an orders array and a next_cursor field when more results exist.
