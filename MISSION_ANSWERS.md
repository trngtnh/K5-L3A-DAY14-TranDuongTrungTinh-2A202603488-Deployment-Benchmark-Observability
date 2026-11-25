# Day 14 Lab - Mission Answers

> **Lưu ý (RULES.md):** Không ghi email, số điện thoại, hoặc secret thật (`.env`, API key, token) vào file này.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found (`01-localhost-vs-production/develop/app.py`)
1. **Hardcode secrets trực tiếp trong source code (dòng 17–18):**
   - `develop/app.py`: `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"`, `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`. Nếu push lên GitHub public sẽ bị lộ thông tin nhạy cảm ngay lập tức.
   - `production/config.py` (dòng 27, 32, 42–43): đọc qua `os.getenv("OPENAI_API_KEY", "")`, `os.getenv("AGENT_API_KEY", "")` và fail-fast nếu `environment == "production"` mà thiếu `AGENT_API_KEY`.
2. **Không quản lý cấu hình qua environment variables (dòng 21–22):**
   - `develop/app.py`: gán cứng `DEBUG = True`, `MAX_TOKENS = 500`.
   - `production/config.py` (dòng 15–35): gom toàn bộ vào dataclass `Settings` đọc từ biến môi trường, có `.env.example` mẫu.
3. **Dùng `print()` thay cho structured logging và log thẳng secret ra stdout (dòng 33–34, 38):**
   - `develop/app.py`: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` làm lộ API key trong log và khó parse/tìm kiếm.
   - `production/app.py` (dòng 28–32, 111–122): dùng `logging` format JSON (`{"time": ..., "level": ..., "msg": ...}`) và không log secrets.
4. **Thiếu endpoint health check / readiness check (dòng 42–43):**
   - `develop/app.py`: chỉ có `/` và `/ask`, nền tảng cloud/load balancer không biết khi nào container còn sống hay sẵn sàng nhận traffic.
   - `production/app.py` (dòng 135–161): có `GET /health` (liveness probe) và `GET /ready` (readiness probe trả `200` hoặc `503`).
5. **Bind `localhost` và hardcode port `8000` (dòng 51–52):**
   - `develop/app.py`: `host="localhost", port=8000` nên không nhận được kết nối từ ngoài container Docker và không nhận `PORT` do Railway/Render cấp động.
   - `production/app.py` (dòng 196–197) + `config.py` (dòng 17–18): mặc định `HOST="0.0.0.0"` và `PORT=int(os.getenv("PORT", "8000"))`.
6. **Bật cứng `reload=True` và tắt đột ngột không có graceful shutdown (dòng 53):**
   - `develop/app.py`: `reload=True` tốn tài nguyên/rủi ro trong production và không xử lý `SIGTERM` hay `lifespan`.
   - `production/app.py` (dòng 39–67, 180–189, 198): `reload=settings.debug`, dùng `lifespan` + `signal.signal(signal.SIGTERM, handle_sigterm)` để hoàn tất request đang chạy rồi mới thoát.

### Exercise 1.2: Run develop version
Lệnh chạy trong `01-localhost-vs-production/develop/` (WSL2 Ubuntu):
```bash
python app.py
curl -sS -i -X POST "http://localhost:8000/ask?question=Hello"
```
Kết quả trả về:
```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:11:23 GMT
server: uvicorn
content-length: 126
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```
Quan sát log server của bản develop thấy dòng lộ secret: `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`.

### Exercise 1.3: Comparison table (Develop vs Production)
| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config / Secrets | Hardcode trong `app.py` (`OPENAI_API_KEY`, `DEBUG=True`, `MAX_TOKENS=500`) | Đọc từ biến môi trường qua `Settings` trong `config.py`, tách dev/staging/prod, có `validate()` fail-fast | Tránh lộ secret lên GitHub; thay đổi cấu hình không cần sửa code hay build lại app |
| Host & Port | Cố định `host="localhost"`, `port=8000` | Đọc `HOST` (mặc định `0.0.0.0`) và `PORT` từ env | Container/cloud cần lắng nghe trên `0.0.0.0` và đúng `PORT` mà nền tảng inject |
| Health check | Không có | `GET /health` (liveness) & `GET /ready` (readiness, trả 503 khi chưa sẵn sàng) | Cloud orchestrator / load balancer biết khi nào cần restart container hoặc ngừng route traffic khi app chưa sẵn sàng |
| Logging | `print("[DEBUG] ...")`, log cả secret | `logging` định dạng JSON (`time`, `level`, `msg`), chỉ log metadata (độ dài câu hỏi, IP), không log secret | Log JSON dễ thu thập, lọc và phân tích trên Datadog/Loki/ELK; tránh rò rỉ dữ liệu nhạy cảm |
| Shutdown | Tắt đột ngột, `reload=True` | `lifespan` + bắt `SIGTERM`, `reload=settings.debug` | Hoàn thành nốt các request đang xử lý (in-flight) và đóng connection sạch sẽ khi deploy/scale down, tránh rớt request của user |
| Input & CORS | Query param `?question=...`, không cấu hình CORS | Body JSON `{"question": "..."}` có kiểm tra 422, cấu hình `CORSMiddleware` từ `ALLOWED_ORIGINS` | Chuẩn hóa API REST, kiểm soát nguồn gọi từ frontend và trả mã lỗi rõ ràng khi input thiếu |

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)
1. **Base image:** `python:3.11` (bản Python đầy đủ gồm OS Debian + compiler/build tools, nặng ~1 GB).
2. **Working directory:** `/app` (`WORKDIR /app` — mọi lệnh `COPY`, `RUN`, `CMD` sau đó thực thi tương đối từ `/app`).
3. **Tại sao `COPY requirements.txt` đứng trước `COPY . .`:** Để tận dụng **Docker layer cache**. `requirements.txt` ít thay đổi hơn source code; nếu copy và chạy `pip install` trước, khi chỉ sửa code Python (`app.py`) thì Docker tái sử dụng layer dependencies đã cache mà không phải tải & cài lại từ đầu.
4. **Khác nhau giữa `CMD` và `ENTRYPOINT`:**
   - `CMD` đặt lệnh/tham số mặc định khi container khởi chạy và **dễ bị ghi đè** khi truyền tham số lúc `docker run <image> <cmd_mới>`.
   - `ENTRYPOINT` cố định file thực thi chính của container; tham số truyền vào `docker run` (hoặc `CMD`) sẽ được nối thêm làm argument cho `ENTRYPOINT` (muốn ghi đè phải dùng cờ `--entrypoint`).

### Exercise 2.2: Build and run develop container
Build từ gốc repo và test container:
```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --rm -p 8000:8000 my-agent:develop
curl -sS -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
```
Kết quả trả về:
```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:14:43 GMT
server: uvicorn
content-length: 103
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

### Exercise 2.3: Image size comparison (đo bằng `docker images`)
- **Develop (`my-agent:develop`):** `1.67 GB` (`1,670 MB` theo `docker images`; `426,319,994` bytes theo `docker inspect .Size`)
- **Production (`my-agent:advanced`):** `240 MB` (theo `docker images`; `57,409,046` bytes theo `docker inspect .Size`)
- **Chênh lệch:** Giảm **`85.6%`** (`(1670 - 240) / 1670`, tương đương giảm 1,430 MB).
- **Giải thích:**
  - **Stage 1 (`builder`):** Dùng `python:3.11-slim`, cài `gcc` và `libpq-dev` rồi chạy `pip install --no-cache-dir --user -r requirements.txt` để biên dịch và cài toàn bộ thư viện vào `/root/.local`.
  - **Stage 2 (`runtime`):** Bắt đầu lại từ `python:3.11-slim` sạch, tạo non-root user `appuser`, chỉ `COPY --from=builder /root/.local /home/appuser/.local`, copy `main.py` và `utils/mock_llm.py`, không mang theo `gcc`, `libpq-dev`, apt cache hay các công cụ build của bản `python:3.11` đầy đủ → image vừa nhẹ hơn rất nhiều vừa giảm bề mặt tấn công (attack surface).

### Exercise 2.4: Docker Compose stack architecture & test
Sơ đồ kiến trúc stack trong `02-docker/production/docker-compose.yml`:
```text
Client (HTTP :80)
   │
   ▼
[ nginx ] (nginx:alpine, reverse proxy + rate limit 10r/s + security headers)
   │  (mạng bridge nội bộ: internal)
   ▼
[ agent ] (multi-stage runtime :8000, depends_on redis & qdrant healthy)
   ├──────────────────────┐
   ▼                      ▼
[ redis ]              [ qdrant ]
(redis:7-alpine :6379) (qdrant/qdrant:v1.9.0 :6333)
 volume: redis_data     volume: qdrant_data
```
Kết quả test qua Nginx (đứng tại `02-docker/production/`):
```bash
docker compose -p k5-day14-docker up -d --build
curl -sS -i http://localhost/health
```
```http
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":1.0,"version":"2.0.0","timestamp":"2026-11-25T08:16:05.243458"}
```
```bash
curl -sS -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
```
```http
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```
Sau khi kiểm tra đã dọn stack bằng `docker compose -p k5-day14-docker down -v`.

---

## Part 3: Cloud Deployment (Agent Part 6)

### Exercise 3.1 / 3.2: Public deployment (`06-lab-complete/`)
- **Platform:** Railway (`k5-day14-agent` — service `agent` + database `Redis`)
- **Public URL:** `https://agent-production-aac81645.up.railway.app`
- **Kiểm tra 4 lệnh CP6 thực tế:**
  - `GET /health` → `200` (`{"status":"ok","environment":"production"}`)
  - `GET /ready` → `200` (`{"status":"ready"}`)
  - `POST /ask` (không key) → `401`
  - `POST /ask` (có `X-API-Key`) → `200`
- **Screenshots:**
  - [Dashboard](screenshots/dashboard.png)
  - [Service running](screenshots/running.png)
  - [Test results](screenshots/test.png)

### So sánh `railway.toml` và `render.yaml`
- **Định dạng & Triết lý cấu hình:**
  - `railway.toml` dùng định dạng **TOML**, cấu hình gọn cho **một service** (nơi đặt file, ví dụ `06-lab-complete/`): chỉ định `[build]` (`builder = "DOCKERFILE"`, `dockerfilePath`) và `[deploy]` (`healthcheckPath = "/health"`, `restartPolicyType`, số lần retry). Các dịch vụ phụ như Redis và biến môi trường thường tạo/liên kết qua Railway Dashboard hoặc Railway CLI (`railway variables set`).
  - `render.yaml` dùng định dạng **YAML (Blueprint / Infrastructure as Code)**, khai báo **toàn bộ hạ tầng nhiều service** trong cùng một file (`services`: web service + Redis/Key-Value service, region, plan, `healthCheckPath: /health`, `autoDeploy`, và toàn bộ danh sách `envVars`).
- **Cách quản lý secrets & biến môi trường:**
  - Với `railway.toml`, secret (`AGENT_API_KEY`, `REDIS_URL`) không nằm trong file config mà đặt trực tiếp trên Railway Dashboard/CLI.
  - Với `render.yaml`, hỗ trợ khai báo schema biến môi trường ngay trong YAML nhưng tránh lộ secret bằng `generateValue: true` (Render tự sinh random key), `sync: false` (nhập tay trên dashboard) hoặc `fromService` (lấy `REDIS_URL` từ service Redis cùng Blueprint).
- **Build & Runtime:** `railway.toml` dùng `builder = "DOCKERFILE"` (hoặc `NIXPACKS`) và lấy `CMD` trực tiếp từ `Dockerfile` (lắng nghe `$PORT`), trong khi `render.yaml` cho phép chọn `runtime: docker` hoặc `runtime: python` kèm `buildCommand` / `startCommand` tường minh.

### Exercise 3.3: GCP Cloud Run CI/CD Pipeline (`03-cloud-deployment/production-cloud-run/`)
Khi push code lên nhánh `main`, Google Cloud Build chạy pipeline 4 bước trong `cloudbuild.yaml`: (1) chạy unit test bằng `pytest` trên container `python:3.11-slim`, (2) build Docker image gắn tag `$COMMIT_SHA` và `latest` (tận dụng `--cache-from`), (3) push các tag image lên Container Registry (`gcr.io/$PROJECT_ID/ai-agent`), và (4) gọi `gcloud run deploy` triển khai lên Cloud Run region `asia-southeast1` với `min-instances=1` (giảm cold start), `max-instances=10`, giới hạn CPU/RAM (`1 vCPU` / `512Mi`) và nạp secret từ Secret Manager. File `service.yaml` mô tả đặc tả Knative/Cloud Run tương ứng (IaC), gồm cấu hình autoscale (`minScale: 1`, `maxScale: 10`, `containerConcurrency: 80`), resource limits/requests, `secretKeyRef`, cùng `livenessProbe` (`/health`) và `startupProbe` (`/ready`).

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/`)
- **Nơi kiểm tra API key:** hàm `verify_api_key` dùng `APIKeyHeader(name="X-API-Key", auto_error=False)` và inject qua `Depends(verify_api_key)` ở `POST /ask`. Thiếu key → `401`, sai key → `403`, đúng key (`AGENT_API_KEY` đọc từ env) → `200`. Để rotate key, đổi giá trị biến môi trường `AGENT_API_KEY` trên nền tảng/server và khởi động lại (hoặc hỗ trợ danh sách key cũ/mới song song trong thời gian chuyển tiếp).
- **Kết quả chạy `curl -i`:**
```bash
# Không có API key → 401
curl -sS -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
```
```http
HTTP/1.1 401 Unauthorized
date: Wed, 25 Nov 2026 08:21:27 GMT
server: uvicorn
content-length: 67
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}
```
```bash
# Có API key (demo local: AGENT_API_KEY=<KEY_DA_CHE>) → 200
curl -sS -i -X POST http://localhost:8000/ask -H "X-API-Key: <KEY_DA_CHE>" -H "Content-Type: application/json" -d '{"question":"Hello"}'
```
```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:21:27 GMT
server: uvicorn
content-length: 145
content-type: application/json

{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2 & 4.3: JWT & Rate limiting (`04-api-gateway/production/`)
- **Thuật toán rate limiting sử dụng (`rate_limiter.py`):** **Sliding Window** (lưu timestamp các request trong `collections.deque` cho mỗi `user_id`; mỗi lần check sẽ `popleft()` các timestamp cũ hơn `now - window_seconds`). Khi đưa lên production đa worker/đa instance phải chuyển sang lưu trong **Redis** (như sorted set) thay vì RAM.
- **Giới hạn theo role:**
  - `user` (tài khoản `student` / `demo123`): **`10` requests / `60` giây** (`rate_limiter_user`).
  - `admin` (tài khoản `teacher` / `teach456`): **`100` requests / `60` giây** (`rate_limiter_admin`). Có thể bypass hoặc cấp hạn mức riêng bằng cách kiểm tra `role == "admin"` ở `app.py` (dòng 141).
- **Output `curl -i` (401, 200 và 429 — ĐÃ CHE TOKEN):**
```bash
# 1. Gọi /ask không có JWT → 401
curl -sS -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
```
```http
HTTP/1.1 401 Unauthorized
date: Wed, 25 Nov 2026 08:21:52 GMT
server: uvicorn
www-authenticate: Bearer
content-length: 76
content-type: application/json

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}
```
```bash
# 2. Lấy JWT ở POST /auth/token (student/demo123), gọi /ask có token → 200
curl -sS -i -X POST http://localhost:8000/ask -H "Authorization: Bearer <TOKEN_DA_CHE>" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
```
```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:21:52 GMT
server: uvicorn
content-length: 192
content-type: application/json

{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```
```bash
# 3. Vòng lặp 12 request liên tiếp: request 1..10 trả HTTP 200, request 11+ trả HTTP 429
curl -sS -i -X POST http://localhost:8000/ask -H "Authorization: Bearer <TOKEN_DA_CHE>" -H "Content-Type: application/json" -d '{"question":"Test 11"}'
```
```http
HTTP/1.1 429 Too Many Requests
date: Wed, 25 Nov 2026 08:21:54 GMT
server: uvicorn
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795594974
retry-after: 59
content-length: 98
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost guard implementation
- **Cách hoạt động (`04-api-gateway/production/cost_guard.py`):**
  - Trước khi gọi LLM, gọi `cost_guard.check_budget(username)`: nếu tổng chi phí toàn hệ thống đạt `global_daily_budget_usd` ($10/ngày) thì chặn với `503`; nếu chi phí ngày của user đạt `daily_budget_usd` ($1/ngày) thì chặn với **`402 Payment Required`**; nếu đạt ≥ 80% thì ghi log cảnh báo.
  - Sau khi LLM trả lời, gọi `cost_guard.record_usage(username, input_tokens, output_tokens)` tính chi phí theo đơn giá token (`$0.00015 / 1K input`, `$0.0006 / 1K output`) và cộng dồn.
  - Khi triển khai production (như trong Part 6), chi phí được lưu trong **Redis** (vd key `budget:<user>:<YYYY-MM>` hoặc ngày, tăng bằng `INCRBYFLOAT` + đặt `EXPIRE`) để dùng chung giữa nhiều worker/instance và không mất khi restart.
- **Vì sao cần cả Rate Limit và Cost Guard:** Rate limit bảo vệ **tốc độ/lưu lượng** (chống spam/DDoS theo số request/phút), còn Cost guard bảo vệ **ngân sách tiền** (vì mỗi request AI có độ dài prompt/output rất khác nhau; kẻ tấn công vẫn có thể gửi ít request nhưng prompt cực dài để đốt hết tiền API).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health vs Readiness checks (`05-scaling-reliability/develop/app.py`)
- **`GET /health` (Liveness probe):** Kiểm tra *"container/process còn sống không?"* — trong bản production chỉ kiểm tra process (hoặc thông số cơ bản như memory), luôn trả `200` khi process còn chạy để tránh cloud platform restart nhầm toàn bộ container chỉ vì dependency bên ngoài tạm thời chập chờn.
- **`GET /ready` (Readiness probe):** Kiểm tra *"agent đã sẵn sàng nhận traffic chưa?"* — kiểm tra `_is_ready` (model/dependencies đã load xong, không đang shutdown) và kết nối dependency như Redis (`_redis.ping()`). Nếu chưa sẵn sàng hoặc Redis mất kết nối thì trả **`503`** để load balancer tạm ngưng đẩy request mới vào instance đó (không restart container).
- Kết quả test trên bản develop:
  - `GET /health` → `200 OK` (`{"status":"ok","uptime_seconds":2.8,"version":"1.0.0",...,"checks":{"memory":{"status":"ok","used_percent":15.5}}}`)
  - `GET /ready` → `200 OK` (`{"ready":true,"in_flight_requests":1}`)

### Exercise 5.2: Graceful shutdown
- **Cơ chế xử lý `SIGTERM`:** Khi platform/Docker gửi `SIGTERM` (`signal 15`), `uvicorn` bắt tín hiệu, kích hoạt phần shutdown trong `lifespan`: đặt `_is_ready = False` (để `/ready` trả `503`, ngừng nhận traffic mới), đợi các request đang xử lý (`_in_flight_requests == 0`, tối đa `timeout_graceful_shutdown=30`s) hoàn tất và trả `200 OK` rồi mới đóng process sạch sẽ.
- **Kết quả test:** Gửi `POST /ask?question=hello` rồi gửi `kill -SIGTERM <PID>` ngay sau `0.3s` → client vẫn nhận được `HTTP/1.1 200 OK`, log server in:
  ```text
  INFO:     Shutting down
  INFO:     127.0.0.1:35768 - "POST /ask?question=hello HTTP/1.1" 200 OK
  INFO:     Waiting for application shutdown.
  2026-11-25 08:23:06,876 INFO 🔄 Graceful shutdown initiated...
  2026-11-25 08:23:06,876 INFO ✅ Shutdown complete
  INFO:     Application shutdown complete.
  INFO:     Finished server process [3853]
  2026-11-25 08:23:06,876 INFO Received signal 15 — uvicorn will handle graceful shutdown
  ```

### Exercise 5.3–5.5: Stateless design & Load balancing (`05-scaling-reliability/production/`)
- **Vì sao state lưu trong RAM bị hỏng khi scale nhiều instance:** Mỗi container/instance có vùng nhớ RAM riêng biệt. Nếu session/lịch sử hội thoại lưu trong `dict` ở RAM của Instance 1, khi Load Balancer (Nginx) điều phối request kế tiếp của cùng user sang Instance 2, Instance 2 sẽ không thấy lịch sử trước đó; ngoài ra mọi state trong RAM cũng mất sạch khi container restart/deploy lại. Giải pháp là chuyển toàn bộ state sang **Redis** dùng chung (`session:{session_id}` với TTL 3600s).
- **Kết quả chạy `docker compose -p k5-day14-scaling up -d --build --scale agent=3` và `python test_stateless.py` (qua Nginx `:8080`, endpoint `POST /chat`):**
  ```text
  ============================================================
  Stateless Scaling Demo
  ============================================================

  Session ID: 73353b02-bfa0-4619-8038-739a79319d96

  Request 1: [instance-1eed2f]
    Q: What is Docker?
    A: Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!...

  Request 2: [instance-4dd78a]
    Q: Why do we need containers?
    A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

  Request 3: [instance-52d3de]
    Q: What is Kubernetes?
    A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

  Request 4: [instance-1eed2f]
    Q: How does load balancing work?
    A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

  Request 5: [instance-4dd78a]
    Q: What is Redis used for?
    A: Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ O...

  ------------------------------------------------------------
  Total requests: 5
  Instances used: {'instance-4dd78a', 'instance-1eed2f', 'instance-52d3de'}
  ✅ All requests served despite different instances!

  --- Conversation History ---
  Total messages: 10
  ...
  ✅ Session history preserved across all instances via Redis!
  ```
- **Giải thích:** 5 request trong cùng một `session_id` được Nginx chia round-robin tới cả **3 instance khác nhau** (`instance-1eed2f`, `instance-4dd78a`, `instance-52d3de`), nhưng khi gọi `GET /chat/{session_id}/history` vẫn đọc đủ **10 messages** (5 user + 5 assistant) nhờ toàn bộ state được lưu tập trung trong Redis. (Đã tắt và dọn stack bằng `docker compose -p k5-day14-scaling down -v`).

---

## Part 6: Production Readiness Check (`06-lab-complete/`)

Chạy `python check_production_ready.py` (chế độ đầy đủ có Docker trong WSL2 Ubuntu):
```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

---

## AI log

<!-- Theo RULES.md: ghi lại các lần dùng AI cho code Part 6 hoặc câu trả lời (hỏi gì, nhận gì, tự sửa và tự kiểm tra thế nào) -->
| Thời điểm | Hỏi gì | AI gợi ý gì | Tự sửa & tự kiểm tra thế nào |
|---|---|---|---|
| 2026-11-25 | Đọc toàn bộ tài liệu hướng dẫn, lập plan step-by-step, cài đặt môi trường WSL2 Ubuntu và chạy kiểm tra Part 1 (`01-localhost-vs-production/`) | Lập checklist CP0–CP8, tạo khung `MISSION_ANSWERS.md`, giải thích các anti-pattern trong `develop/app.py` và đối chiếu với `production/app.py` | Tự kiểm tra thực tế bằng cách chạy cả `develop/app.py` và `production/app.py` trong WSL2 Ubuntu, gọi `/ask`, `/health`, `/ready`, kiểm tra log và `SIGTERM` trước khi điền bảng |
| 2026-11-25 | Thực hiện Part 2 (`02-docker/`): build image develop & multi-stage, đo dung lượng và chạy Compose stack | Giải thích cấu trúc Dockerfile, layer caching, `CMD` vs `ENTRYPOINT` và cơ chế giảm size ở multi-stage build | Tự chạy `docker build`, `docker run`, `docker images`, `docker compose -p k5-day14-docker up` trong WSL2 và lấy số đo thực tế (`1.67 GB` vs `240 MB`) cùng output curl |
| 2026-11-25 | Thực hiện Part 4 (`04-api-gateway/`): kiểm tra API Key, JWT (`POST /auth/token`), Rate limit và Cost Guard | Giải thích luồng `verify_api_key`, JWT, thuật toán Sliding Window trong `rate_limiter.py` và cơ chế chặn chi phí 402 trong `cost_guard.py` | Tự chạy server `develop` và `production` trong WSL2, chụp output `curl -i` thật cho mã `401`, `200`, `429` và che key/token trước khi lưu |
| 2026-11-25 | Thực hiện Part 5 (`05-scaling-reliability/`): kiểm tra `/health`, `/ready`, `SIGTERM` và stack stateless `--scale agent=3` | Phân biệt liveness vs readiness, cơ chế graceful shutdown và lý do tách state ra Redis | Tự chạy `develop/app.py` test `kill -SIGTERM`, chạy `docker compose -p k5-day14-scaling up -d --build --scale agent=3` và `python test_stateless.py` thu kết quả thật |
| 2026-11-25 | Hoàn thiện Part 6 (`06-lab-complete/`): `config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py`, `cost_guard.py`, `main.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Thiết kế Redis Lua sliding-window atomic rate limiter, cost guard theo ngày/tháng, `JsonFormatter`, `SIGTERM` handler giữ lại uvicorn handler, multi-stage Dockerfile non-root | Chạy `python check_production_ready.py` (đầy đủ với Docker trong WSL2) đạt `PASS 12/12` (`43/43 checks`), đồng thời test `docker compose up` end-to-end (`200`/`401`/`422`), sửa thiếu env passthrough trong `docker-compose.yml` và chạy lại `check_production_ready.py` PASS 12/12 |
| 2026-11-25 | So sánh `railway.toml` vs `render.yaml` và đọc `cloudbuild.yaml`, `service.yaml` cho Part 3 (`03-cloud-deployment/`) | Tổng hợp điểm khác biệt giữa Railway TOML và Render Blueprint YAML, và tóm tắt 4 bước CI/CD Cloud Build → Cloud Run | Đối chiếu trực tiếp từng field trong `06-lab-complete/railway.toml`, `06-lab-complete/render.yaml`, `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml` |
| 2026-11-25 | Deploy `06-lab-complete/` lên Railway (`k5-day14-agent`) kèm Redis và viết `DEPLOYMENT.md` | Hướng dẫn tạo project Railway, thêm Redis service, liên kết `REDIS_URL=${{Redis.REDIS_URL}}`, set `AGENT_API_KEY`, tạo domain và chạy kiểm tra `--public-url` | Tự kiểm tra thực tế endpoint public `https://agent-production-aac81645.up.railway.app` (`/health` 200, `/ready` 200, `/ask` 401/200) và chạy `check_production_ready.py --public-url` |
| 2026-11-25 | Thực hiện Part 7 (`06-lab-complete/benchmark-observability/`): benchmark local (CP7), fix KB, và chạy stack Prometheus/Grafana/Jaeger/Locust + chaos experiments (CP8) | Giải thích tail latency, trade-off RAG v1 vs v2, PromQL golden signals, cách đọc trace/span liên kết `trace_id` với JSON log và phân tích các thí nghiệm alert/scaling | Tự chạy toàn bộ `load_test.py`, `run_eval.py`, `promtool test rules`, `locust` headless (`scale=1`, `scale=3`), thí nghiệm A–E trên Docker stack; sửa `PYTHONUSERBASE` trong `production/Dockerfile` để container `agent` chạy được; đối chiếu toàn bộ số liệu trong `BENCHMARK_REPORT.md` khớp 100% với file JSON/CSV |
