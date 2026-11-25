# Deployment Information

## Public URL
https://agent-production-aac81645.up.railway.app

## Platform
Railway

## Test Commands

### 1. Health Check (Liveness)
```bash
curl -sS -i https://agent-production-aac81645.up.railway.app/health
# Expected: HTTP 200 — {"status":"ok","environment":"production"}
```

### 2. Readiness Check (Redis connectivity)
```bash
curl -sS -i https://agent-production-aac81645.up.railway.app/ready
# Expected: HTTP 200 — {"status":"ready"}
```

### 3. Auth Check (without API key → 401)
```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://agent-production-aac81645.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'
# Expected: 401
```

### 4. API Test (with authentication → 200)
```bash
curl -X POST https://agent-production-aac81645.up.railway.app/ask \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
# Expected: HTTP 200
```

## Environment Variables Set
- `PORT` (injected automatically by Railway)
- `REDIS_URL` (linked from Railway Redis service `${{Redis.REDIS_URL}}`)
- `AGENT_API_KEY` (set on Railway service Variables)
- `ENVIRONMENT` (`production`)
- `LOG_LEVEL` (`INFO`)
- `RATE_LIMIT_PER_MINUTE` (`10`)
- `DAILY_BUDGET_USD` (`1.0`)
- `MONTHLY_BUDGET_USD` (`10.0`)

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
