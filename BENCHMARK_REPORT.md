# Benchmark & Observability Report — Day 14

> Copy file này thành `BENCHMARK_REPORT.md` trong repo của bạn và điền đầy đủ.
> Mọi con số phải lấy từ report JSON / CSV / screenshot thật, không ước lượng.

**Họ tên:** Trần Dương Trung Tính (MSSV: 2A202603488)
**Ngày chạy:** 2026-11-25
**Môi trường:** Windows 10 + WSL2 Ubuntu 24.04, 20 vCPU / 8GB RAM (7.6 GiB WSL2), Docker Desktop 28.0.4

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | ≤ 500 ms | 95% người dùng nhận phản hồi dưới nửa giây (trải nghiệm chat mượt), khớp mặc định `--slo-p95-ms 500` của `load_test.py` khi mock LLM base latency ~120–200ms + retrieval ~20ms |
| Error rate `/ask` | ≤ 2.0 % | Tỷ lệ lỗi upstream mock mặc định là 1% (`MOCK_ERROR_RATE=0.01`), đặt 2.0% cho phép dao động nhỏ mà không báo động giả nhưng vẫn chặn được sự cố diện rộng |
| Accuracy trên golden dataset | ≥ 80 % (sau khi sửa KB: 100%) | Đảm bảo agent trả lời đúng kiến thức cốt lõi và bắt buộc không có regression (`--baseline`) trước khi cho phép deploy |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.29 | 189.9 | 217.7 | 1692.0 | 0.33% | PASS |
| 10 | 42.62 | 190.2 | 218.7 | 1696.0 | 1.00% | PASS |
| 50 | 115.62 | 190.0 | 273.2 | 1659.1 | 0.00% | PASS |
| 100 | 71.8 | 327.9 | 2269.2 | 2468.4 | 1.33% | FAIL |

**Nhận xét:** RPS **không tăng tuyến tính** theo concurrency. Từ concurrency 1 → 10 → 50, vì mock LLM dùng `asyncio.sleep` nên RPS tăng mạnh (4.29 → 42.62 → 115.62 req/s) trong khi p50 giữ quanh 190 ms. Tuy nhiên lên tới concurrency = 100 trên 1 process uvicorn duy nhất, event loop + hàng đợi kết nối bắt đầu bão hòa (saturation), làm p50 tăng lên 327.9 ms, p95 vọt lên 2269.2 ms (vượt SLO 500 ms → FAIL) và throughput tụt xuống còn 71.8 req/s.

### 2.2 Tail latency

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.9 | 218.1 | 223.0 |
| 0.03 (mặc định) | 228.9 | 221.2 | 1705.6 |
| 0.1 | 355.4 | 1696.3 | 1712.6 |

**Nhận xét:** **`p99` và `p95` nhạy nhất** với request chậm: ở `MOCK_SLOW_RATE=0.03`, `avg` chỉ tăng thêm ~40 ms (188.9 → 228.9 ms) nhưng `p99` nhảy vọt hơn 7.6 lần (223.0 → 1705.6 ms); khi `MOCK_SLOW_RATE=0.1` (>5% request chậm), cả `p95` cũng vọt lên 1696.3 ms trong khi `avg` mới là 355.4 ms. Không nên đặt SLO theo `average` vì trung bình cộng bị kéo xuống bởi ~90–97% request nhanh, che giấu trải nghiệm tệ của nhóm người dùng gặp tail latency (~1.7 giây).

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.6 | 108.7 | 113.5 |
| Tổng cost (USD) | $0.000411 | $0.000756 | $0.000789 |
| Cost / câu đúng (USD) | $0.00041055 | $0.00003599 | $0.00003432 |
| Quality gate | FAIL | PASS (≥ 80%) | PASS (≥ 100%, 0 regression) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm entry `["jwt"]` vào `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` (và đồng bộ sang `production/utils/mock_llm.py`) giải thích JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn |
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm entry `["hpa", "kubernetes", "autoscal"]` vào `KNOWLEDGE_BASE` mô tả Kubernetes HPA scale pod dựa trên CPU, memory hoặc custom metrics |

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- Agent v2 tốn gần **gấp đôi token/câu** (108.7–113.5 vs 57.6 tokens) và **tổng cost** cao hơn (~$0.000789 vs $0.000411), đồng thời thêm ~20 ms retrieval latency.
- Đổi lại, **accuracy tăng từ 4.3% lên 100.0%** (23/23), giúp **cost trên mỗi câu trả lời đúng** giảm từ `$0.00041055` xuống `$0.00003432` (**rẻ hơn ~12 lần**). Đây là trade-off rất đáng giá của RAG so với gọi LLM trần.

**Quality gate trong CI/CD:**
- Khi chạy `python eval/run_eval.py --url http://localhost:8001 --baseline reports/eval_v2.json` (mô phỏng phiên bản bị tụt chất lượng), script trả về **exit code `1`** vì accuracy < ngưỡng và phát hiện regression.
- Trong GitHub Actions (`.github/workflows/eval.yml` chạy trên `pull_request`), CI bật agent v2 trên port 8000 rồi chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2.json`; nếu exit code ≠ 0, step fail và chặn merge PR.

---

## 4. Observability stack (production)

### 4.1 Screenshot & Quan sát Dashboard (Exercise 7.4)

- [x] [Dashboard Grafana khi đang có tải](screenshots/grafana-dashboard.png) (thấy rõ Golden Signals)
- [x] [Một trace chậm trong Jaeger](screenshots/jaeger-slow-trace.png) (thấy span nào chiếm thời gian)
- [x] [Một trace lỗi trong Jaeger](screenshots/jaeger-error-trace.png) (có event `exception`)
- [x] [Trang Alerts của Prometheus với 3 alert cùng đang **firing**](screenshots/prometheus-alert-firing.png)

**Đọc dashboard & câu hỏi Exercise 7.4:**
- **Giá trị đọc trên Grafana khi đang có tải 30 users:** Traffic `18.8 req/s`, Error rate `0.87%`, Latency p95 `291.79 ms` (trong khi avg ~230 ms, p50 ~175 ms, p99 ~1.75–1.95 s), In-flight `2`, Instances up `2`, LLM cost (1h) `$0.6674`.
- **Phân bố tải theo instance:** Nginx chia đều traffic giữa 2 container (`172.19.0.4:8000` ~`9.34 req/s` và `172.19.0.8:8000` ~`9.27 req/s`).
- **Khoảng cách giữa avg (~238 ms) và p95/p99:** Khoảng cách lớn giữa p50/avg với p99 (~1.7 s) phản ánh tác động của tail latency (~3% request chậm do mock LLM), cho thấy trung bình cộng che khuất trải nghiệm chậm ở nhóm percentile cao.
- **Vì sao input tokens/s (~650 tok/s) lớn hơn output tokens/s (~340 tok/s):** Agent v2 dùng pipeline RAG nên prompt gửi vào LLM ghép thêm `SYSTEM_PROMPT` + tài liệu context lấy từ knowledge base cùng câu hỏi, khiến input tokens dài hơn câu trả lời output.
- **Vì sao `curl http://localhost:8080/metrics` trả về `404`:** Trong `nginx/nginx.conf`, block `location = /metrics { return 404; }` chặn truy cập `/metrics` từ bên ngoài để tránh lộ thông tin nội bộ (route, lưu lượng, version); chỉ Prometheus ở mạng nội bộ `obs_net` scrape trực tiếp `agent:8000/metrics`.

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (`for: 1m`) | **91s** (~1.5 phút) | **Error rate** (Golden Signals đỏ ~20–22%) & **Requests/s theo status code** (`503` tăng mạnh) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (`for: 2m`) | **151s** (~2.5 phút) | **Latency p95** (vọt lên `~1.93s` > 1s) & **Latency /ask — p50 / p95 / p99** |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (`for: 2m`) | **~151s** (~2.5 phút, đạt `$23.49/giờ`) | **LLM cost (1h)**, **Cost burn rate (USD/giờ)** & **LLM latency p95 theo model** |
| D | `docker pause` 1 instance | `AgentInstanceDown` (`for: 30s`) | **40s** | **Instances up** (giảm từ `2` xuống `1`); lưu ý panel **Error rate** của agent vẫn xanh (`0.83%`) dù Nginx trả lỗi `504` cho các request gửi tới container đang pause trước timeout |
| E | `AGENT_VERSION=v1` | **Không có alert mới nào firing** | — (chỉ `agent_info` đổi sang `v1`) | **Retrieval hit ratio** (biến mất vì v1 không retrieve), **Tokens/s** giảm, bảng **Instances / version** đổi sang `v1` |

*(Ghi chú thí nghiệm D: Lỗi `504 Gateway Timeout` phát sinh ở tầng Nginx khi đợi quá `proxy_read_timeout 10s` vì container bị pause không phản hồi, request chưa bao giờ được agent xử lý xong nên metric của agent không đếm được — cần giám sát thêm ở tầng Load Balancer / synthetic check từ ngoài).*

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**
- Các alert hạ tầng (`ErrorRate`, `LatencyP95`, `InstanceDown`, `CostBurnRate`) chỉ đo việc hệ thống *chạy nhanh và không lỗi 5xx*, không biết câu trả lời có *đúng* không.
- Để phát hiện tụt chất lượng như chuyển nhầm về `v1` (hoặc hỏng RAG/prompt):
  1. Chạy **automated eval (quality gate)** trên `golden_dataset.jsonl` trong CI/CD trước khi deploy (so sánh `--baseline`, chặn nếu accuracy tụt hoặc có regression).
  2. Theo dõi các metric nghiệp vụ/chất lượng trên production: `agent_retrieval_total{outcome="hit"|"miss"}` (tỷ lệ hit tụt về 0 là dấu hiệu hỏng retrieval), **user feedback** (thumbs up/down) và **online eval / LLM-as-judge** lấy mẫu ngẫu nhiên trên traffic thật.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất tìm được:

- `trace_id`: `a359cf09738789482f9fae2c9a71878d`
- Tổng thời gian: `1725.69 ms` (`1.7s` hiển thị trên Jaeger UI; `POST /ask`)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (`1698.20 ms`, `98.4%` — bước `agent.retrieve_context` chỉ mất `26.00 ms`, `1.5%`)
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T11:22:32.764337+00:00", "level": "INFO", "service": "observable-agent", "instance": "b392f3fc9a38", "event": "llm_call", "trace_id": "a359cf09738789482f9fae2c9a71878d", "span_id": "635a38d8dc8b6287", "request_id": "fc1ae291a40e", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1697.8}
```

Trace lỗi mẫu đối chiếu (`jaeger-error-trace.png`): `trace_id` = `2c4333a1f6c42a9e3b53e0f83eadf8da` (`POST /ask` `503`, `124 ms`), event `exception` tại span `chat mock-llm-small` (`LLMError: Upstream LLM error: 503 Service Unavailable (mock)`), khớp log JSON `"event": "llm_error"`.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

*(Số liệu lấy từ dòng `Aggregated` — và riêng `POST /ask` trong ngoặc — của `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`)*:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 65.61 *(POST /ask: 59.65)* | 190 *(190)* | 220 *(220)* | 1700 *(1700)* | 64 / 7861 (0.81%) |
| 3 | 65.80 *(POST /ask: 59.71)* | 190 *(190)* | 220 *(220)* | 1700 *(1700)* | 75 / 7882 (0.95%) |

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không làm giảm p95/p99** (`p50=190ms`, `p95=220ms`, `p99=1700ms` giữ nguyên ở cả hai lần chạy) và RPS gần như không đổi (~65.6 vs ~65.8 req/s). Lý do:
1. **Nút thắt nằm ở upstream LLM latency**, không phải CPU của agent: mỗi request đều phải chờ LLM trả lời (`~120–180ms` + retrieval `~20ms`, cộng thêm 3% request chậm `+1500ms`), và FastAPI dùng async I/O nên 1 container chưa bị nghẽn ở mức ~60 req/s.
2. Ngoài ra, 100 user của Locust có `wait_time = between(0.5, 2)` nên tổng tải sinh ra từ phía client cũng bị chặn quanh ~65 req/s.
3. Lợi ích thực sự của việc scale lên 3 instance là **tăng khả năng chịu tải đồng thời tối đa (capacity)** và **high availability** (khi 1 instance chết/pause, 2 instance còn lại vẫn gánh traffic). Muốn giảm p95/p99 của từng request thì phải tối ưu chính bước gọi LLM (đổi model nhanh hơn, streaming, semantic cache, timeout/retry hoặc fallback model).

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Latency trung bình che giấu tail latency:** phải đặt SLO và giám sát theo **p95 / p99**; đồng thời đo hiệu quả RAG bằng **cost / câu trả lời đúng** chứ không chỉ tổng cost hay token.
  2. **Observability hạ tầng xanh không đồng nghĩa chất lượng tốt:** cần kết hợp 3 trụ cột (**Metrics – Traces – Logs** liên kết qua `trace_id`) với **eval trên golden dataset** trong CI/CD và **retrieval/quality monitoring** trên production.
  3. **Alert cần độ trễ `for` hợp lý và phải được unit test (`promtool`)**: tránh alert fatigue do spike ngắn, đồng thời kết hợp giám sát cả ở tầng load balancer/synthetic check để bắt các lỗi như Nginx `504` khi backend treo.
- Nếu đưa agent này lên production thật, tôi sẽ thêm:
  - **Metrics/Alerts ở tầng Nginx / Synthetic blackbox probe** (bắt lỗi `502`/`504` và độ trễ end-to-end từ ngoài vào).
  - **Metrics theo dõi chất lượng & bảo mật:** tỷ lệ `retrieval_miss_rate`, số lượng `401` / `429` / `402` theo thời gian (phát hiện tấn công hoặc spam), và **alert Redis down / Cost guard gần chạm ngưỡng 80%**.
  - **Trace sampling (vd 10% request thường + 100% request lỗi/chậm)** để giảm chi phí lưu trữ khi traffic lớn, kèm **Alertmanager** đẩy cảnh báo sang Slack/PagerDuty.
