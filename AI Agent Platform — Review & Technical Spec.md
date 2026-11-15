# AI Agent Platform — Review & Technical Spec

Nov 15, 2026 · @Kien

## 1. Review và các quyết định chính

Hướng đi đúng, nhưng có một điểm phải đổi và ba điểm cần chốt lại trước khi code. Mục 3 đã chốt cho bản demo: Codex qua proxy, kèm rủi ro và đường lui nêu ở mục 5.

| Nhu cầu | Đánh giá | Quyết định đề xuất |
| --- | --- | --- |
| 1. Chat UI + agent đầy đủ tính năng | Hợp lý, nhưng danh sách tính năng quá rộng cho bản đầu. Skill, memory, RAG, MCP đều quy về một cơ chế: agent loop + tool registry + context builder. | Xây lõi là agent loop có tool calling và streaming. Mọi thứ khác cắm vào dưới dạng tool hoặc context provider, làm theo phase. |
| 2. Data Topic Platform | Ý tưởng tốt và là phần tạo giá trị nhất. Chất lượng trả lời phụ thuộc vào semantic layer (định nghĩa cột, metric, glossary), không phụ thuộc model. | Topic là một service riêng, lộ ra cho agent qua một MCP server nội bộ với 4 tool cố định. Agent Platform không biết gì về SQL Server. |
| 3. LLM hub dùng Codex (OpenAI) làm proxy | Chạy được về kỹ thuật và đủ cho demo. Hai điểm yếu: một tài khoản ChatGPT phục vụ nhiều người là trái điều khoản của OpenAI, và hạn mức subscription dễ chạm trần khi 50 user dùng chung. | Demo: Codex qua proxy, đặt sau một LLM Gateway (LiteLLM) để đổi sang API key không phải sửa code. Quota chung = giới hạn request theo user trên tài khoản dùng chung. Quota riêng = user tự nhập OpenAI API key của họ (BYOK). |
| 4. SQL Server hay thêm Postgres | Thêm Postgres là đúng. Agent không nên query thẳng DB vận hành, và delete-insert sẽ cho kết quả sai nếu đọc giữa chừng. | Postgres làm ba việc: DB của app, bản sao phục vụ query (serving layer), và vector store (pgvector). Sync bằng snapshot + atomic swap cho bảng delete-insert, incremental cho bảng còn lại. |

Các quyết định anh đã chốt cho bản đầu:

- Quy mô: khoảng 50 user mỗi ngày; bảng nguồn cỡ 100k dòng; kết quả trả về là dữ liệu tổng hợp.
- LLM: Codex qua proxy (kiểu CLIProxyAPI hoặc 9router) cho bản demo, chỉ dùng model của OpenAI; công ty chưa có LLM gateway.
- Chat UI: trải nghiệm giống Claude web, dùng được trên điện thoại.
- Đăng nhập: email + mã kích hoạt hoặc Google OAuth; chỉ email được admin whitelist mới vào được.
- Topic có Python sandbox để agent vẽ chart, phân tích và xuất Excel. Chat nhận ảnh và PDF đính kèm, dung lượng tối đa là config.
- Nhân lực và hạ tầng: một người làm bằng vibe coding; chạy trên một Ubuntu VPS.

## 2. Kiến trúc tổng thể

Hệ thống gồm 5 service: Chat UI, Agent Platform, LLM Gateway, Data Topic Platform và Sync worker, dùng chung một Postgres. Agent Platform không chứa logic về dữ liệu; nó chỉ gọi tool.

&#91;embedded content: kiến trúc tổng thể · 4 tầng, 3 cổng ra\]

Luồng một câu hỏi về dữ liệu:

1. User chọn topic (hoặc để agent tự chọn) và gửi câu hỏi. API kiểm tra session và quyền.
2. Input guardrail quét câu hỏi. Context builder ghép system prompt, skill đang bật, memory của user và danh sách tool user được phép dùng.
3. Orchestrator gọi LLM qua Gateway. Gateway kiểm tra quota, chọn key (chung hoặc BYOK), ghi token usage.
4. Model gọi `get_topic_context` để lấy schema, định nghĩa metric, glossary và câu SQL mẫu của topic.
5. Model viết SQL và gọi `run_sql`. Topic service kiểm tra quyền, validate SQL, chạy trên Postgres bằng role chỉ đọc, trả về tối đa N dòng.
6. Model tổng hợp câu trả lời, stream về UI kèm SQL đã chạy, thời điểm dữ liệu được sync và 2 đến 3 câu hỏi follow-up.
7. Toàn bộ lượt chat, tool call và chi phí được ghi vào audit log.

Stack đề xuất:

| Thành phần | Lựa chọn | Lý do |
| --- | --- | --- |
| Chat UI | Next.js + Vercel AI SDK (hoặc assistant-ui) | Có sẵn streaming, tool-call rendering; responsive cho điện thoại |
| Agent Platform | Python, FastAPI, SSE | Hệ sinh thái LLM và data tốt nhất, team BI quen Python |
| Agent loop | Tự viết trên OpenAI SDK, gọi qua Gateway | Loop đơn giản, dễ kiểm soát quyền và log |
| LLM Gateway | LiteLLM Proxy | Quota theo user, một endpoint OpenAI-compatible; đổi upstream bằng config |
| Tool protocol | MCP (streamable HTTP) | Một cơ chế cho cả Topic và tích hợp sau này |
| Database | Postgres 16 + pgvector + pg\_trgm | App DB, serving, vector và full-text trong một nơi |
| Sync | Worker Python + APScheduler | Bảng cỡ 100k dòng copy xong trong vài giây; chưa cần Airflow hay CDC |
| Queue, cache | Redis | Rate limit, cache kết quả, job ingest |
| Auth | Email + mã kích hoạt, hoặc Google OAuth; admin whitelist | Không lưu mật khẩu; chỉ email trong whitelist mới vào được |

Triển khai: Docker Compose trên một Ubuntu VPS, Caddy làm reverse proxy và HTTPS, backup Postgres hàng ngày ra ngoài máy. Với 50 user, một VPS khoảng 4 vCPU và 8 GB RAM là đủ để bắt đầu (ước lượng, chưa đo).

## 3. Spec: Agent Platform

Lõi là một agent loop; skill, memory, RAG và MCP chỉ là ba cách đưa thông tin vào loop đó. Thiết kế đúng ba interface dưới đây thì mọi tính năng trong danh sách đều cắm vào được mà không sửa lõi.

| Interface | Việc nó làm | Ai cắm vào |
| --- | --- | --- |
| `ContextProvider` | Trả về đoạn text đưa vào system prompt trước mỗi lượt | System prompt, skill, user memory, topic đang chọn |
| `Tool` | Hàm có schema JSON mà model gọi được | MCP server, API nội bộ, RAG search, ghi memory |
| `Guard` | Chặn hoặc sửa input, tool call, output | Chống prompt injection, lọc PII, SQL validator |

### 3.1 Agent loop

1. Nhận message, nạp lịch sử hội thoại (cắt hoặc tóm tắt khi vượt ngưỡng token).
2. Chạy input guards.
3. Context builder ghép prompt theo thứ tự cố định: system prompt nền, skill, mô tả tool, memory, rồi mới đến hội thoại.
4. Gọi LLM có streaming. Nếu model trả tool call: kiểm tra quyền, chạy guard cho tool, thực thi, đưa kết quả về model, lặp lại.
5. Dừng khi model trả lời xong hoặc chạm giới hạn (mặc định 12 vòng, 120 giây, ngân sách token mỗi lượt).
6. Chạy output guards, sinh follow-up, ghi log.

Bản đầu chỉ cần một agent. "Orchestrator" nhiều agent con chỉ thêm khi một agent không đủ, ví dụ câu hỏi cần ghép nhiều topic.

### 3.2 Tính năng và cách hiện thực

| Tính năng | Cách làm | Phase |
| --- | --- | --- |
| Streaming | SSE từ FastAPI; event: `text_delta`, `tool_start`, `tool_result`, `followups`, `done`, `error` | 1 |
| System prompt | Lưu theo version trong DB; admin sửa trên UI; mỗi lượt chat ghi lại version đã dùng | 1 |
| Tools (MCP, API) | Bảng `tool_servers`; admin đăng ký URL MCP; platform liệt kê tool và lọc theo quyền user | 1 |
| Phân quyền | RBAC 3 role (admin, builder, user) + ACL theo tài nguyên (topic, tool server, skill) | 1 |
| Prompt caching | OpenAI cache tự động theo tiền tố prompt: giữ phần tĩnh (system prompt, skill, tool) ở đầu và cố định thứ tự. Không chèn timestamp hay nội dung thay đổi vào phần này | 1 |
| Follow-up | Một lần gọi model rẻ sau câu trả lời, trả JSON 3 câu hỏi | 2 |
| Skill | Thư mục gồm `SKILL.md` (tên, mô tả, hướng dẫn) + file kèm. Chỉ nạp tên và mô tả vào prompt; model đọc nội dung đầy đủ khi cần | 2 |
| User memory | Tool `memory_read` / `memory_write` trên bảng `memories`, phạm vi user hoặc topic. User xem và xoá được trên UI | 2 |
| RAG tài liệu | Tool `search_docs`: hybrid search (pgvector + full-text) trên chunk đã ingest, lọc theo ACL trước khi rank | 3 |
| Guardrails | Xem mục 7 | 1 đến 3 |
| Orchestrator đa agent | Agent chính gọi agent con như một tool, mỗi agent con có context riêng | 4 |

### 3.3 API chính

| Method | Path | Mô tả |
| --- | --- | --- |
| POST | `/v1/conversations` | Tạo hội thoại, tuỳ chọn gắn `topic_ids`, `skill_ids` |
| POST | `/v1/conversations/{id}/messages` | Gửi message, trả về SSE stream |
| GET | `/v1/conversations/{id}` | Lịch sử kèm tool call |
| POST | `/v1/messages/{id}/feedback` | Thumbs up/down + ghi chú, dùng cho đánh giá |
| GET, POST | `/v1/admin/tool-servers` | Đăng ký và phân quyền MCP server |
| GET, POST | `/v1/skills`, `/v1/memories` | Quản lý skill và memory |
| GET | `/v1/usage` | Token và chi phí theo user, team, ngày |

### 3.4 Chat UI

Giao diện theo bố cục của Claude web: sidebar bên trái, khung chat ở giữa, mọi thứ dùng được trên điện thoại.

| Khu vực | Hành vi |
| --- | --- |
| Sidebar | Nút chat mới, danh sách recent chat nhóm theo ngày, tìm kiếm, đổi tên, xoá. Trên điện thoại thu thành drawer |
| Khung chat | Streaming, nút dừng, tạo lại câu trả lời, copy, feedback. Khối tool call gập được: SQL đã chạy, bảng kết quả, thời điểm sync, chart |
| Tiêu đề và tóm tắt | Model nhỏ đặt tiêu đề sau lượt đầu. Tóm tắt hội thoại được cập nhật khi hội thoại dài, hiển thị ở đầu chat và dùng để nén context |
| Xoá hội thoại | Ẩn ngay với user (soft delete), xoá hẳn message và tool output sau 30 ngày. Khi xoá, hỏi user có xoá luôn memory sinh từ hội thoại đó không |
| Memory | Trang Settings liệt kê memory, xoá từng dòng, tắt memory toàn cục. Mỗi hội thoại có công tắc "không lưu memory" |
| Điện thoại | Responsive từ 360 px, cài được như PWA, ô nhập dính đáy, bảng cuộn ngang, chart co theo màn hình |
| Admin | User và whitelist, role, tool server, system prompt, quota, audit |

### 3.5 Đăng nhập và whitelist

Không dùng mật khẩu: user đăng nhập bằng mã gửi qua email hoặc bằng Google, và cả hai đường đều qua cùng một cửa whitelist.

1. Admin thêm email (hoặc cả domain) vào `allowlist`, kèm role mặc định.
2. User nhập email. Hệ thống gửi mã 6 số, hết hạn sau 10 phút, tối đa 5 lần nhập sai. Hoặc user bấm đăng nhập bằng Google.
3. Sau khi xác thực được email, hệ thống kiểm tra `allowlist`. Có trong danh sách thì tạo session. Không có thì tài khoản ở trạng thái `pending` và xuất hiện trong hàng chờ duyệt của admin.
4. Session là cookie httpOnly, hết hạn sau 7 ngày. Admin gỡ email khỏi whitelist thì mọi session của user đó bị thu hồi.

Form đăng nhập trả cùng một thông báo cho email có và không có trong whitelist, để không lộ danh sách. Gửi mail qua SMTP của một dịch vụ transactional email; giới hạn số mã gửi cho mỗi email và mỗi IP.

### 3.6 Session và auto compact

Mỗi hội thoại là một session có trạng thái riêng, và khi context chạm 75% cửa sổ của model thì hệ thống tự nén phần cũ. User vẫn thấy toàn bộ lịch sử; chỉ phần gửi cho model bị nén.

Trạng thái của một session gồm: message, topic và skill đang bật, công tắc memory, các `result_id` và artifact đã sinh, bản tóm tắt nén gần nhất, số token hiện dùng. Mở lại session từ sidebar thì mọi thứ này được khôi phục, kể cả file kết quả để sandbox dùng tiếp.

Auto compact chạy theo hai bước, bước rẻ trước:

1. **Cắt tool output cũ** khi vượt 50%: kết quả SQL và stdout của các lượt cũ được thay bằng một dòng tham chiếu (`result_id`, số dòng, tên cột). Dữ liệu gốc vẫn nằm trong artifact.
2. **Tóm tắt** khi vượt 75%: model `small` nén các lượt cũ thành một bản tóm tắt có cấu trúc, giữ nguyên văn 6 lượt gần nhất.

Bản tóm tắt bắt buộc có: mục tiêu của user, topic và định nghĩa metric đã dùng, quy ước user đã nêu, các câu SQL quan trọng kèm `result_id`, kết luận đã đạt, việc còn dang dở. Thiếu các mục này thì agent sẽ hỏi lại hoặc tính lại sau khi nén.

| Hành vi | Chi tiết |
| --- | --- |
| Hiển thị | Thanh đo context trong khung chat; một dòng đánh dấu "đã nén ngữ cảnh" tại vị trí nén, bấm vào xem bản tóm tắt |
| Nén thủ công | Nút nén ngay, và nút "chat mới từ bản tóm tắt này" |
| Lưu trữ | Bảng `compactions`: conversation\_id, upto\_message\_id, summary, tokens\_before, tokens\_after. Message gốc không bị xoá |
| Thời điểm | Chạy giữa hai lượt, không chạy giữa một chuỗi tool call đang dở |
| Ngưỡng | 50%, 75% và số lượt giữ lại là config theo model alias |
| Chi phí | Mỗi lần nén làm mất prompt cache của phần hội thoại một lần; phần system prompt và tool vẫn giữ cache |

### 3.7 Đính kèm ảnh và PDF

User đính kèm được ảnh và file PDF vào message, với dung lượng tối đa là một config (`MAX_UPLOAD_MB`, đề xuất mặc định 20 MB mỗi file). Nội dung file được coi là dữ liệu không tin cậy, giống kết quả tool.

| Hạng mục | Quy định |
| --- | --- |
| Định dạng | PNG, JPEG, WebP, PDF. Kiểm tra bằng magic bytes, không tin đuôi file |
| Giới hạn | `MAX_UPLOAD_MB` mỗi file, tối đa 5 file mỗi message, PDF tối đa 50 trang. Cả ba là config |
| Upload | `POST /v1/files` (multipart), trả về `file_id`; message tham chiếu `file_id`. Caddy và API cùng đặt trần kích thước request |
| Lưu trữ | Volume riêng trên VPS; bảng `files`: user\_id, conversation\_id, mime, size, sha256, page\_count, storage\_path |
| Quyền | Chỉ chủ hội thoại đọc được file. Xoá hội thoại thì xoá file theo cùng lịch 30 ngày |

Cách đưa file vào model:

1. **Ảnh**: thu nhỏ về cạnh dài tối đa 2000 px rồi gửi dưới dạng image input.
2. **PDF có lớp text**: trích text theo trang. File ngắn thì đưa thẳng vào context; file dài thì chia chunk, embed và cho agent tìm qua `search_docs` trong phạm vi hội thoại.
3. **PDF scan hoặc trang có bảng, biểu đồ**: render trang thành ảnh và gửi như ảnh, giới hạn số trang mỗi lượt.

Ba điểm cần lưu ý khi làm:

- Image input đi qua Codex proxy cần được kiểm thử sớm; nếu proxy không chuyển được ảnh thì các request có ảnh đi qua OpenAI API key dự phòng.
- Khi auto compact, file đính kèm cũ được thay bằng tham chiếu `file_id` kèm một dòng mô tả; agent đọc lại file khi cần.
- Sandbox mount được file đính kèm ở chế độ chỉ đọc, ví dụ để trích bảng từ PDF ra Excel.

Trên UI: nút đính kèm, kéo thả, dán ảnh từ clipboard, chụp ảnh trên điện thoại, thumbnail xem trước và thông báo rõ khi file vượt giới hạn.

## 4. Spec: Data Topic Platform và cách tích hợp

Tích hợp bằng cách biến Data Topic Platform thành một MCP server nội bộ. Agent Platform coi nó như mọi MCP server khác, nên sau này thêm nguồn realtime không cần đổi kiến trúc.

### 4.1 Topic là gì

Một topic là gói tri thức có version, gồm 5 phần:

| Phần | Nội dung | Nguồn |
| --- | --- | --- |
| Datasets | Danh sách bảng trong serving schema, khoá join giữa các bảng, grain của từng bảng | Parse tự động + user xác nhận |
| Column dictionary | Tên, kiểu, mô tả, giá trị mẫu, enum, cột nhạy cảm | Parse tự động, LLM gợi ý mô tả, user sửa |
| Metrics | Tên, công thức SQL, grain, filter mặc định, đơn vị | User nhập |
| Glossary | Thuật ngữ nghiệp vụ và từ đồng nghĩa (tiếng Việt, tiếng Anh, viết tắt) | User nhập |
| Verified queries | Cặp câu hỏi + SQL đã được duyệt, dùng làm few-shot | User nhập, bổ sung dần từ feedback |

Verified queries là phần tăng độ chính xác nhiều nhất. Bắt buộc tối thiểu 5 cặp trước khi cho publish.

### 4.2 Vòng đời topic

1. **Load**: user chọn nguồn (bảng SQL Server, Google Sheet, CSV, Hive). Hệ thống nạp vào serving schema của Postgres và đăng ký lịch sync.
2. **Parse**: profile dữ liệu (kiểu, null rate, cardinality, min/max, giá trị mẫu), đoán khoá join.
3. **Define**: user nhập định nghĩa cột, metric, glossary. LLM soạn bản nháp để user sửa thay vì gõ từ đầu.
4. **Build**: sinh knowledge base, embed mô tả cột, metric, glossary và verified queries vào pgvector.
5. **Review**: chạy bộ câu hỏi test của topic, hiển thị kết quả; owner duyệt rồi publish version.
6. **Share**: owner cấp quyền `viewer` hoặc `editor` cho user hoặc group.

Trạng thái: `draft` → `building` → `in_review` → `published` → `archived`. Sửa topic đã publish sẽ tạo version mới; hội thoại cũ vẫn trỏ về version cũ.

### 4.3 Bốn tool lộ ra cho agent

| Tool | Input | Output |
| --- | --- | --- |
| `list_topics` | (không) | Topic user được quyền xem: id, tên, mô tả ngắn, thời điểm sync gần nhất |
| `get_topic_context` | `topic_id`, `question` | Schema rút gọn, metric và glossary liên quan, 3 đến 5 verified query gần nhất với câu hỏi |
| `run_sql` | `topic_id`, `sql` | Tối đa 200 dòng, tổng số dòng, `data_as_of`, hoặc lỗi kèm gợi ý sửa |
| `search_docs` | `topic_id`, `query` | Đoạn tài liệu liên quan kèm nguồn (phase 3) |

Điểm thiết kế quan trọng:

- User identity đi kèm mọi tool call (JWT ngắn hạn do Agent Platform ký). Topic service tự kiểm tra ACL, không tin agent.
- Topic nhỏ (dưới khoảng 30 cột) thì trả toàn bộ schema. Topic lớn thì retrieve theo câu hỏi để tiết kiệm token.
- Memory và skill gắn theo topic: memory có `scope = topic:{id}`, ví dụ "tuần tính từ thứ Hai". Skill gắn topic là playbook phân tích lặp lại, ví dụ "báo cáo driver supply hàng tuần".
- Câu trả lời user bấm thumbs-up được đưa vào hàng chờ để owner duyệt thành verified query.

### 4.4 Đo chất lượng

Mỗi topic có một eval set (câu hỏi + kết quả đúng). Chạy lại khi đổi topic version, đổi system prompt hoặc đổi model. Mục tiêu để publish: đúng từ 85% trở lên trên eval set của topic.

### 4.5 Chart và Python sandbox

Làm hai mức: chart bằng spec trước, sandbox Python sau. Mức đầu không chạy code nên rẻ và an toàn, và đã phủ phần lớn nhu cầu vẽ bar, line, pie.

|  | Mức 1: chart spec (phase 2) | Mức 2: Python sandbox (phase 3) |
| --- | --- | --- |
| Tool | `render_chart(result_id, spec)` | `run_python(topic_id, code, result_ids)` |
| Cách chạy | Model viết Vega-Lite JSON; frontend render | Code chạy trong container dùng một lần |
| Dùng khi | Chart chuẩn từ một kết quả SQL | Biến đổi dữ liệu, thống kê, chart tuỳ biến, xuất file Excel |
| Rủi ro | Không đáng kể | Thực thi code do model viết; cần cô lập |

Cách dữ liệu đi vào sandbox: `run_sql` lưu toàn bộ kết quả thành file Parquet kèm một `result_id`, còn model chỉ nhận 200 dòng đầu. Sandbox mount file đó ở chế độ chỉ đọc, nên agent phân tích được trên cả 100k dòng mà không tốn token và không cần credential database.

Yêu cầu cô lập cho mỗi lần chạy:

- Một container mới, bị huỷ sau khi chạy; không có network; user không phải root; filesystem chỉ đọc trừ thư mục `/out`.
- Giới hạn 1 CPU, 1 GB RAM, 30 giây, số process tối đa; bỏ hết Linux capabilities.
- Image dựng sẵn pandas, numpy, matplotlib, plotly, openpyxl, xlsxwriter; không cho cài thêm package lúc chạy.
- Sandbox runner là service riêng và là nơi duy nhất được gọi Docker. Container API không mount `docker.sock`.
- Output gồm stdout (cắt ở 10.000 ký tự) và file trong `/out` (PNG, HTML, CSV, XLSX). File được lưu thành artifact, hiển thị trong chat và tải về được.

Skill vẽ chart là một skill gắn topic: hướng dẫn chọn loại chart, template code với màu và định dạng số thống nhất. Thư mục skill được mount chỉ đọc vào sandbox.

Xuất Excel đi qua sandbox: agent gọi `run_python` để đọc file Parquet theo `result_id` và ghi file `.xlsx` vào `/out`. Vì đọc từ file kết quả đầy đủ, bản Excel có đủ mọi dòng dù model chỉ nhìn thấy 200 dòng đầu. File xuất hiện trong chat dưới dạng artifact tải về được; kích thước tối đa của file xuất là config.

## 5. Spec: LLM Gateway

Gateway là một LiteLLM Proxy mà Agent Platform gọi qua giao thức OpenAI-compatible; upstream phía sau là thứ đổi được bằng config. Bản demo dùng Codex qua proxy theo quyết định của anh, và thiết kế này giữ sẵn đường chuyển sang API key.

Mô hình token có hai tầng và dùng cả hai cùng lúc. Tầng upstream: một token OAuth của tài khoản Codex nằm trong proxy (CLIProxyAPI hoặc 9router), cả 50 user đi chung qua đó. Tầng platform: mỗi user có một token riêng do platform cấp (virtual key của LiteLLM), dùng để tính usage, đặt quota và khoá từng người mà không đụng tới token upstream. User không bao giờ thấy token nào trong hai loại; trình duyệt chỉ giữ session cookie.

### 5.1 Rủi ro của hướng subscription và đường lui

LLM hub chỉ dùng OpenAI, với hai upstream khai báo trong LiteLLM:

- Chính: tài khoản ChatGPT đăng nhập qua OAuth trong proxy, lộ ra endpoint OpenAI-compatible.
- Dự phòng: một OpenAI API key có đặt trần chi tiêu.
- Hai upstream cùng họ model, nên prompt và tool schema không đổi khi Gateway chuyển qua lại.

Với Codex, các tool như [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) đăng nhập tài khoản ChatGPT qua OAuth rồi lộ ra một endpoint OpenAI-compatible, nên về kỹ thuật ghép vào LiteLLM được ngay. Rủi ro anh cần biết trước khi demo:

- Điều khoản của OpenAI không cho chia sẻ credential hay để người khác dùng tài khoản của mình. Một tài khoản phục vụ 50 user qua proxy rơi đúng vào trường hợp này, và tài khoản có thể bị khoá.
- Hạn mức của subscription tính theo cửa sổ thời gian cho một người dùng. 50 user dùng chung có thể chạm trần giữa buổi demo.
- Proxy dựa vào luồng đăng nhập không chính thức, có thể hỏng khi OpenAI thay đổi phía server.

Đường lui: chuẩn bị sẵn một API key có đặt trần chi tiêu, khai báo trong LiteLLM làm fallback. Khi proxy lỗi hoặc tài khoản bị giới hạn, Gateway tự chuyển và Agent Platform không phải sửa gì. Nếu demo thành công và mở rộng, chuyển hẳn sang API key.

### 5.2 Hai loại quota

|  | Quota chung | Quota riêng (BYOK) |
| --- | --- | --- |
| Credential | Demo: tài khoản Codex qua proxy. Sau demo: API key của công ty | API key cá nhân user tự tạo ở OpenAI Platform |
| Ai trả tiền | Công ty | User hoặc team của user |
| Giới hạn | Số request và token mỗi ngày cho từng user, do LiteLLM đếm | Do provider áp lên key đó |
| Lưu trữ | Secret manager | Mã hoá envelope trong Postgres, chỉ Gateway giải mã, không bao giờ trả về UI |
| Khi nào dùng | Mặc định | User chủ động chọn, hoặc tự động khi quota chung hết (nếu user bật) |

### 5.3 Hành vi

- Mỗi user có một virtual key của LiteLLM, tạo khi tài khoản được kích hoạt. Agent Platform gọi Gateway bằng key đó, nên usage được tính đúng người dù upstream là một tài khoản chung.
- Trước mỗi request Gateway kiểm tra quota của user. Hết quota trả lỗi `quota_exceeded`; UI hiển thị và gợi ý chuyển sang key cá nhân.
- Thứ tự upstream: Codex proxy, rồi OpenAI API key dự phòng. Tên model trong code là alias (`main`, `small`) để đổi model thật mà không sửa Agent Platform.
- `main` cho agent loop; `small` cho follow-up, tiêu đề, tóm tắt hội thoại và phân loại guardrail.
- Proxy chỉ nghe trên mạng nội bộ của Docker, không mở cổng ra internet; file token OAuth nằm trong volume riêng và được backup.
- Ghi lại cho mỗi request: user, conversation, model, token input và output, độ trễ. Prompt caching qua proxy có thể không hoạt động như API chính thức; đo lại sau khi dựng.
- Không log nội dung prompt ở Gateway; nội dung nằm trong audit log của Agent Platform, nơi có phân quyền.

## 6. Data infrastructure

Thêm một Postgres và không cho agent chạm vào SQL Server. "Realtime" nên hiểu là độ trễ có cam kết cho từng bảng, vì đa số câu hỏi phân tích chấp nhận trễ vài phút.

Với quy mô đã chốt (bảng cỡ 100k dòng, kết quả dạng tổng hợp), bản đầu dùng một cách duy nhất cho mọi bảng: full snapshot + atomic swap, chạy lệch sau job nguồn. Chưa biết job nguồn có chạy trong một transaction hay không, nên luôn bật chốt chặn row count ở mục 6.2 và nhờ DBA bật snapshot isolation. Incremental và CDC trong bảng 6.3 để dành cho khi có bảng lớn hơn nhiều.

### 6.1 Bố cục Postgres

| Schema | Chứa gì | Ai ghi | Ai đọc |
| --- | --- | --- | --- |
| `app` | User, hội thoại, topic, skill, memory, audit, usage | Agent Platform, Topic service | Hai service đó |
| `serving` | Bản sao bảng nguồn, dữ liệu từ Sheet / CSV / Hive | Sync worker | Role `agent_ro` (chỉ SELECT) |
| `kb` | Chunk tài liệu, embedding, chỉ mục full-text | Job ingest | Topic service |

Role `agent_ro` đặt `default_transaction_read_only = on`, `statement_timeout = 30s`, và chỉ có quyền trên `serving`.

### 6.2 Vấn đề delete-insert và cách xử lý

Rủi ro nằm ở hai chỗ: đọc từ nguồn đúng lúc bảng đang rỗng một phần, và ghi vào bản sao theo cách khiến agent thấy trạng thái dở dang.

**Đọc từ SQL Server.** Nếu job nguồn chạy delete + insert trong một transaction, đọc bằng snapshot isolation sẽ luôn thấy bản nhất quán (cần DBA bật `ALLOW_SNAPSHOT_ISOLATION`). Nếu không bật được, hoặc job commit delete rồi mới insert, thì thêm chốt chặn ở Sync worker:

- So số dòng với lần sync trước; lệch quá ngưỡng (ví dụ giảm hơn 30%) thì bỏ lần này, thử lại sau 30 đến 60 giây.
- Đọc hai lần cách nhau vài giây; số dòng và checksum khác nhau nghĩa là bảng đang được ghi, thử lại.
- Không dùng `NOLOCK`; nó cho phép đọc dữ liệu chưa commit.

**Ghi vào Postgres.** Nạp vào bảng staging `serving.t__new` bằng `COPY`, kiểm tra xong mới đổi tên trong một transaction: `t` thành `t__old`, `t__new` thành `t`, rồi drop `t__old`. Agent chỉ thấy bản cũ hoặc bản mới, không bao giờ thấy bảng rỗng. Đặt `lock_timeout` ngắn cho bước đổi tên và không tạo view phụ thuộc trực tiếp vào các bảng này.

### 6.3 Chọn cách sync theo kiểu bảng

| Kiểu bảng | Cách sync | Độ trễ điển hình | Cần gì ở nguồn |
| --- | --- | --- | --- |
| Delete-insert toàn bảng mỗi XX phút | Full snapshot + atomic swap, lịch lệch sau job nguồn | Chu kỳ nguồn + thời gian copy | Quyền SELECT |
| Có `updated_at` hoặc `rowversion`, ít khi xoá | Incremental theo watermark, upsert | 1 đến 5 phút | Quyền SELECT, index trên cột watermark |
| Update và delete rải rác, cần gần realtime | Change Tracking, hoặc CDC qua Debezium + Kafka | Vài giây đến 1 phút | DBA bật Change Tracking hoặc CDC |
| Cần đúng tại thời điểm hỏi | Không sao chép; gọi API hoặc MCP của hệ thống nguồn | Tức thời | API từ team sở hữu |

Không dùng CDC cho bảng delete-insert: mỗi chu kỳ sẽ sinh ra số event gấp đôi số dòng của bảng. Bắt đầu bằng hai cách đầu; chỉ dựng Debezium khi có bảng thực sự cần.

Mỗi lần sync ghi một dòng vào `app.sync_runs` (bảng, thời điểm bắt đầu và kết thúc, số dòng, trạng thái). `run_sql` trả về `data_as_of` từ đây để agent nói rõ dữ liệu tính đến lúc nào.

### 6.4 Khác biệt SQL Server và Postgres cần xử lý

- So sánh chuỗi: SQL Server thường không phân biệt hoa thường, Postgres thì có. Dùng `citext` cho cột hay được lọc, hoặc ghi rõ trong column dictionary.
- Kiểu dữ liệu: `datetime` sang `timestamp`, `nvarchar` sang `text`, `bit` sang `boolean`, `money` sang `numeric`, `uniqueidentifier` sang `uuid`.
- Múi giờ: quy ước một múi giờ cho serving và ghi vào topic.
- Agent chỉ viết SQL theo dialect Postgres; verified queries cũng viết bằng Postgres.

### 6.5 RAG trên Postgres

- `pgvector` với index HNSW cho embedding; cột `tsvector` + GIN cho full-text; `pg_trgm` cho tìm gần đúng tên riêng.
- Hybrid search: lấy top 30 từ mỗi nhánh, gộp bằng reciprocal rank fusion, trả top 8.
- Mỗi chunk mang `topic_id` và `acl`; lọc quyền trong câu WHERE, trước khi rank.
- Hai loại nội dung được embed: metadata của topic (cột, metric, glossary, verified queries) và tài liệu user upload (PDF, doc, wiki).

Postgres đủ cho bảng serving tới cỡ vài chục triệu dòng. Nếu câu hỏi tổng hợp trên bảng lớn bị chậm, thêm bảng tổng hợp sẵn trước khi nghĩ đến engine dạng cột.

## 7. Bảo mật và guardrails

Không có bộ lọc nào chặn hết prompt injection, nên lớp bảo vệ chính là quyền hạn: agent chỉ làm được những gì user đang chat được phép làm. Bộ lọc nội dung là lớp bổ sung.

| Lớp | Biện pháp | Phase |
| --- | --- | --- |
| Quyền hạn | Mọi tool call chạy với danh tính user; ACL kiểm tra ở service đích, không ở prompt | 1 |
| SQL an toàn | Parse bằng `sqlglot`: chỉ cho một câu SELECT, chỉ bảng thuộc topic, tự thêm `LIMIT`, cấm hàm hệ thống. Chạy bằng role `agent_ro` có timeout | 1 |
| Dữ liệu không tin cậy | Kết quả tool, nội dung RAG, giá trị trong bảng được bọc trong tag riêng; system prompt ghi rõ đó là dữ liệu, không phải chỉ dẫn | 1 |
| Tool có tác động | Tool ghi hoặc gửi ra ngoài (tạo ticket, gửi mail) cần user bấm xác nhận trên UI | 1 |
| Chống rò rỉ | Không cho agent vừa đọc dữ liệu nhạy cảm vừa gọi tool gửi ra ngoài trong cùng lượt mà không xác nhận. Không render ảnh hay link tự động từ output | 2 |
| Cột nhạy cảm | Đánh dấu trong column dictionary; mask hoặc loại khỏi serving (số điện thoại, CMND, địa chỉ) | 2 |
| Input guard | Model nhỏ phân loại câu hỏi: injection rõ ràng, ngoài phạm vi, yêu cầu lộ system prompt | 2 |
| Output guard | Quét PII và secret trong câu trả lời; kiểm tra số liệu trong câu trả lời có khớp kết quả tool | 3 |
| Giới hạn | Rate limit theo user, giới hạn vòng lặp và token mỗi lượt, giới hạn số dòng trả về | 1 |
| Audit | Lưu đầy đủ prompt version, tool call, SQL, số dòng, chi phí; admin tra cứu được | 1 |

MCP server do người khác viết là nguồn rủi ro riêng: chỉ admin được đăng ký, mỗi server có danh sách tool được bật, và mô tả tool được duyệt trước khi đưa vào prompt.

## 8. Data model chính

Mười bốn bảng trong schema `app` đủ cho đến hết phase 3. Mọi bảng có `id uuid`, `created_at`, `updated_at`.

| Bảng | Cột chính | Ghi chú |
| --- | --- | --- |
| `users` | email, name, role, team\_id, status | Tạo khi xác thực email; status pending cho đến khi có trong allowlist |
| `teams` | name, monthly\_budget\_usd | Nhóm để chia quota và share |
| `conversations` | user\_id, title, topic\_ids\[\], skill\_ids\[\], prompt\_version\_id |  |
| `messages` | conversation\_id, role, content jsonb, model, tokens\_in, tokens\_out, tokens\_cached, cost\_usd | content giữ cả block text và tool |
| `tool_calls` | message\_id, server\_id, tool\_name, input jsonb, output\_ref, status, duration\_ms | Output lớn lưu ngoài, chỉ giữ tham chiếu |
| `tool_servers` | name, url, transport, auth\_config, enabled\_tools\[\], status | MCP server đã đăng ký |
| `acl` | resource\_type, resource\_id, principal\_type, principal\_id, permission | Dùng chung cho topic, tool server, skill |
| `prompt_versions` | name, content, version, is\_active | System prompt có version |
| `skills` | name, description, body, files\_ref, scope, owner\_id | scope: global, team, topic, user |
| `memories` | scope, scope\_id, content, source\_message\_id | scope: user hoặc topic |
| `topics` | name, description, owner\_id, status, current\_version\_id |  |
| `topic_versions` | topic\_id, version, datasets jsonb, columns jsonb, metrics jsonb, glossary jsonb, published\_at | Bất biến sau khi publish |
| `verified_queries` | topic\_id, question, sql, status, embedding | Few-shot và eval set |
| `sync_runs` | dataset, mode, started\_at, finished\_at, row\_count, status, error | Nguồn của `data_as_of` |

Schema `kb` có hai bảng: `documents` (topic\_id, title, source\_uri, acl) và `chunks` (document\_id, content, embedding vector, tsv tsvector, metadata jsonb). Usage và budget do LiteLLM quản lý trong DB riêng của nó.

Bổ sung cho các yêu cầu mới:

| Bảng hoặc cột | Nội dung | Phục vụ |
| --- | --- | --- |
| `allowlist` | email hoặc domain, default\_role, added\_by | Whitelist do admin quản lý |
| `email_codes` | email, code\_hash, expires\_at, attempts | Mã kích hoạt; chỉ lưu hash |
| `sessions` | user\_id, token\_hash, expires\_at, revoked\_at | Thu hồi khi gỡ whitelist |
| `artifacts` | conversation\_id, tool\_call\_id, kind, storage\_path, size | Chart, file từ sandbox, kết quả SQL dạng Parquet |
| `conversations.summary` | text, cập nhật theo lượt | Tóm tắt hội thoại |
| `conversations.use_memory` | boolean | Công tắc không lưu memory |
| `conversations.deleted_at` | timestamp | Soft delete, xoá hẳn sau 30 ngày |

## 9. Roadmap và danh sách công việc

Năm phase, khoảng 15 tuần cho một người làm bằng vibe coding; đây là ước lượng và phụ thuộc số giờ anh dành mỗi tuần. Thứ tự được chọn để tuần thứ 5 đã có người dùng thật hỏi trên một topic thật, và phase 4 có thể để sau demo. Mỗi task nên giao cho coding agent kèm đúng mục spec liên quan và một test chấp nhận.

### Phase 0: Nền tảng (1 tuần)

Xong khi: các câu hỏi ở mục 10 có câu trả lời và môi trường dev chạy được.

- [ ] Dựng VPS Ubuntu: Docker Compose, Caddy (HTTPS), firewall, backup Postgres hàng ngày
- [ ] Xin quyền đọc SQL Server; nhờ DBA bật snapshot isolation; hỏi chủ bảng xem job delete-insert có chạy trong một transaction không
- [ ] Đo số dòng và chu kỳ delete-insert thực tế của các bảng nguồn
- [ ] Dựng Postgres 16 với pgvector, pg\_trgm, citext; tạo ba schema và role `agent_ro`
- [ ] Dựng repo, CI, docker-compose (Postgres, Redis, LiteLLM, Codex proxy, API, UI)
- [ ] Auth: email + mã kích hoạt, Google OAuth, allowlist, hàng chờ duyệt của admin

### Phase 1: MVP hỏi đáp trên một topic (4 tuần)

Xong khi: 5 đến 10 user thử nghiệm hỏi được trên topic Driver supply, có streaming, có log chi phí.

- [ ] LiteLLM trước Codex proxy: virtual key và quota theo user, alias model, endpoint usage, API key dự phòng làm fallback
- [ ] Agent loop: streaming, tool calling, giới hạn vòng lặp, prompt caching
- [ ] API hội thoại và SSE; lưu `conversations`, `messages`, `tool_calls`
- [ ] Chat UI responsive: sidebar recent chat, khung chat, khối tool call, đổi tên và xoá hội thoại, feedback
- [ ] Sync worker: full snapshot + atomic swap cho các bảng của topic đầu; `sync_runs`
- [ ] Topic MCP server với `list_topics`, `get_topic_context`, `run_sql`; topic đầu tiên định nghĩa bằng file YAML
- [ ] SQL validator bằng sqlglot; ACL cơ bản; audit log
- [ ] 20 verified queries và eval set cho topic đầu

### Phase 2: Data Topic Platform tự phục vụ (4 tuần)

Xong khi: một người ngoài team tự tạo, publish và share được một topic mà không cần kỹ sư hỗ trợ.

- [ ] UI tạo topic: chọn nguồn, xem profile, nhập định nghĩa cột, metric, glossary
- [ ] Connector: bảng SQL Server, Google Sheet, CSV; Hive làm sau nếu cần
- [ ] LLM soạn nháp mô tả cột và metric từ profile dữ liệu
- [ ] Topic version, trạng thái, luồng review và publish
- [ ] Share topic theo user và group; bộ chọn topic trên Chat UI; render chart từ Vega-Lite spec
- [ ] Lịch sync theo từng dataset; cảnh báo khi sync lỗi hoặc row count bất thường
- [ ] Retrieve theo câu hỏi cho topic lớn (embedding metadata)
- [ ] BYOK: nhập, mã hoá và chọn key cá nhân

### Phase 3: Skill, memory, guardrails (3 tuần)

Xong khi: agent nhớ được quy ước của user và topic, và bộ test injection nội bộ không còn ca nào làm lộ dữ liệu ngoài quyền.

- [ ] Skill: định dạng, lưu trữ, nạp theo nhu cầu, UI quản lý
- [ ] Memory: tool đọc và ghi, phạm vi user và topic, UI xem và xoá
- [ ] Follow-up question; tiêu đề và tóm tắt hội thoại tự động; công tắc không lưu memory; PWA; auto compact hai bước và thanh đo context; đính kèm ảnh và PDF
- [ ] Python sandbox: runner cô lập, lưu artifact, skill vẽ chart, xuất Excel; input guard, masking cột nhạy cảm, xác nhận cho tool có tác động
- [ ] Hàng chờ duyệt verified query từ feedback
- [ ] Trang admin: prompt version, tool server, quota, tra cứu audit

### Phase 4: RAG tài liệu và tích hợp mở (3 tuần)

Xong khi: agent trả lời được từ tài liệu có trích nguồn và gọi được ít nhất một MCP server realtime bên ngoài.

- [ ] Pipeline ingest tài liệu: parse, chunk, embed, ACL
- [ ] `search_docs` với hybrid search và trích nguồn trong câu trả lời
- [ ] Đăng ký MCP server bên ngoài; duyệt tool; OAuth theo user nếu server yêu cầu
- [ ] Output guard; chạy eval tự động khi đổi prompt, model hoặc topic version
- [ ] Dashboard vận hành: độ trễ, tỉ lệ lỗi, chi phí, tỉ lệ thumbs-up theo topic
- [ ] Agent con cho câu hỏi ghép nhiều topic (nếu số liệu sử dụng cho thấy cần)

## 10. Rủi ro và câu hỏi mở

Rủi ro lớn nhất là câu trả lời sai nhưng nghe có vẻ đúng; ba rủi ro còn lại là pháp lý, dữ liệu dở dang và chi phí.

| Rủi ro | Hậu quả | Giảm thiểu |
| --- | --- | --- |
| SQL đúng cú pháp nhưng sai nghiệp vụ | Người dùng ra quyết định trên số sai | Verified queries, eval set theo topic, luôn hiển thị SQL và `data_as_of`, metric định nghĩa sẵn thay vì để model tự suy |
| Dùng credential sai điều khoản | Khoá tài khoản, gián đoạn dịch vụ | Đã chấp nhận cho demo; giữ API key dự phòng trong Gateway và chuyển hẳn khi mở rộng |
| Dữ liệu nội bộ gửi ra LLM ngoài | Vi phạm chính sách công ty | Anh đánh giá không quan trọng với project này; vẫn mask cột nhạy cảm |
| Đọc bảng giữa lúc delete-insert | Số liệu thiếu hoặc bằng 0 | Chốt chặn row count, atomic swap, `data_as_of` |
| Chi phí token tăng nhanh | Vượt ngân sách | Budget theo user, prompt caching, model nhỏ cho việc phụ, giới hạn số dòng trả về model |
| Topic nhiều nhưng định nghĩa kém | Mất niềm tin vào platform | Bắt buộc review và eval trước khi publish; owner chịu trách nhiệm topic |
| Prompt injection qua dữ liệu hoặc MCP ngoài | Lộ dữ liệu, hành động ngoài ý muốn | Quyền theo user, xác nhận tool có tác động, duyệt MCP server |

Còn hai việc cần xác minh ở phase 0; các câu hỏi khác anh đã trả lời và đã đưa vào mục 1:

- [ ] Số dòng thật và chu kỳ delete-insert của từng bảng nguồn
- [ ] Job delete-insert có chạy trong một transaction không (hỏi chủ bảng)
