curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secure-api-key-here" \
  -d '{"jsonrpc": "2.0", "method": "run_redshift_query", "params": {"query": "SELECT * FROM sales LIMIT 10"}, "id": 1}'
