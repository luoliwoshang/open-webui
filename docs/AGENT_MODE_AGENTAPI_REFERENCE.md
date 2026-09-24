# Open WebUI Agent 模式 AgentAPI 接口契约

本文是 Open WebUI Agent 模式后端适配器使用的七牛 AgentAPI 接口参考。接口和响应结构来自 `https://agent.qiniuapi.com` 的实际测试，测试日期为 2026-09-24。

本文只记录 Open WebUI 当前方案需要的上游接口，不包含 Open WebUI 自己的 `/api/v1/agentapi/*` 路由设计。响应中的字段可能随 Agent 配置增加，适配器应保留未知字段，不能依赖未列出的字段。

## 1. 连接和认证

```text
Base URL: https://agent.qiniuapi.com
认证: Authorization: Bearer <AGENT_API_KEY>
```

API Key 只能由 Open WebUI 后端持有。浏览器请求必须先经过 Open WebUI 身份和会话权限校验，不能直接请求七牛接口。

JSON 请求使用：

```http
Content-Type: application/json
Authorization: Bearer <AGENT_API_KEY>
```

## 2. 官方 SDK 验证

已使用官方 SDK 完成创建 Session、提交事件、读取事件分页、读取文件和删除 Session 的闭环测试：

```text
Python: anthropic 1.8.0
TypeScript: @anthropic-ai/sdk 0.128.0
```

两套 SDK 都会自动请求 `?beta=true`，并发送 AgentAPI 所需的 managed agents beta header。

### Python SDK

```python
from anthropic import Anthropic

client = Anthropic(
    api_key=os.environ["AGENTAPI_API_KEY"],
    base_url="https://agent.qiniuapi.com",
)

session = client.beta.sessions.create(
    agent={"type": "agent", "id": agent_id, "version": agent_version},
    environment_id=environment_id,
)

page = client.beta.sessions.events.list(
    session.id,
    limit=20,
    order="asc",
)
```

测试确认 `page.data` 是事件列表，`page.next_page` 是上游游标。`page.model_dump(mode="json")` 返回包含 `data` 和 `next_page` 的分页对象。

发送事件时，SDK 的类型参数使用 `content`，而 AgentAPI 线路请求使用 `input.parts`。使用 `extra_body` 后，实际发出的请求体只有 AgentAPI 结构：

```json
{
  "events": [
    {
      "type": "user.message",
      "input": {
        "parts": [{ "type": "text", "text": "任务内容" }]
      }
    }
  ]
}
```

### TypeScript SDK

```ts
const response = await client.beta.sessions.events.send(
  session.id,
  {
    events: [
      {
        type: 'user.message',
        content: [{ type: 'text', text: '任务内容' }],
      },
    ],
  },
  {
    body: {
      events: [
        {
          type: 'user.message',
          input: { parts: [{ type: 'text', text: '任务内容' }] },
        },
      ],
    },
  },
);
```

TypeScript 分页对象的原始信封在 `page.body`：

```ts
const page = await client.beta.sessions.events.list(session.id, {
  limit: 20,
  order: 'asc',
});

page.body.data;
page.body.next_page;
```

`for await (const event of page)` 适合遍历事件，但会隐藏分页信封。Open WebUI 后端代理必须使用 `page.body`（或 Python 的 `page.model_dump()`）保留 `next_page`，不能只把迭代器产出的事件数组返回给前端。

### 完整透传策略

SDK 类型对象可以用于后端内部的类型检查和业务判断。若需要严格保留上游原始 JSON、未知扩展字段和原始响应头，使用 SDK 的 `with_raw_response`：

```python
raw = client.beta.sessions.events.with_raw_response.list(
    session_id,
    limit=20,
    order="asc",
)

payload = raw.json()
```

测试确认 `raw.json()` 保留原始 `data`、`next_page` 和事件字段；文件下载使用 `client.beta.files.download(file_id)` 返回二进制响应，可直接流式代理。推荐后端适配器同时保留 typed page 和 raw page 两条路径：业务逻辑使用 typed page，代理响应使用 raw JSON。

## 3. Session 列表

### 请求

```http
GET /v1/sessions?limit={limit}&page={page}
```

`limit` 用于限制返回数量。首次请求可以不传 `page`；后续请求把响应中的 `next_page` 原样作为 `page` 参数传回。

### 响应 `200`

```json
{
  "data": [
    {
      "id": "ses_fd75aa2265bf4fbd82942040",
      "type": "session",
      "agent": {
        "type": "agent",
        "id": "agt_23e6d8a187ac4382bf9665a9",
        "name": "短剧",
        "runtime": "claudecode",
        "version": 4,
        "model": {
          "id": "deepseek/deepseek-v4-flash-20260731",
          "effort": { "type": "medium" }
        }
      },
      "environment_id": "env_75ab58e00b7e4af1bf0153d9",
      "deployment_id": null,
      "status": "idle",
      "title": "Hello！",
      "metadata": {},
      "resources": [],
      "vault_ids": [],
      "usage": {},
      "stats": {
        "active_seconds": 0,
        "duration_seconds": 0
      },
      "created_at": "2026-09-24T23:01:41.391+08:00",
      "updated_at": "2026-09-24T23:01:41.391+08:00",
      "archived_at": null
    }
  ],
  "next_page": "ses_fd75aa2265bf4fbd82942040"
}
```

Open WebUI 不使用全量 Session 列表作为员工会话列表。它只使用本地 `chat` 索引筛选当前用户有权限的 Session，再按下面的单 Session 接口读取上游内容。

## 4. 创建 Session

### 请求

```http
POST /v1/sessions
```

请求体：

```json
{
  "agent": {
    "id": "agt_23e6d8a187ac4382bf9665a9",
    "version": 4
  },
  "environment_id": "env_75ab58e00b7e4af1bf0153d9",
  "metadata": {
    "open_webui_chat_id": "<local-chat-id>",
    "open_webui_user_id": "<owner-user-id>"
  }
}
```

`agent.id`、`agent.version` 和 `environment_id` 使用创建 Agent 配置时的快照值。`metadata` 可以写入 Open WebUI 的本地关联信息，但不能写入 API Key 或敏感内容。

### 响应 `200`

响应是完整的 Session 对象，结构与 `GET /v1/sessions/{session_id}` 相同。创建测试返回的核心字段如下：

```json
{
  "id": "ses_e2b13b6e62fe44008352e829",
  "type": "session",
  "agent": {
    "type": "agent",
    "id": "agt_23e6d8a187ac4382bf9665a9",
    "version": 4
  },
  "environment_id": "env_75ab58e00b7e4af1bf0153d9",
  "status": "idle",
  "metadata": {
    "purpose": "api-contract-test"
  },
  "created_at": "2026-09-24T23:03:02.096382096+08:00",
  "updated_at": "2026-09-24T23:03:02.096382096+08:00",
  "archived_at": null
}
```

Open WebUI 收到 `id` 后，才创建本地 `chat.mode = 'agent'` 索引。若本地写入失败，调用删除接口清理孤儿 Session。

### 已验证错误

缺少 `environment_id` 返回：

```http
400 Bad Request
environment_id is required
```

只发送扁平 `agent_id` 而不是 `agent: {id, version}` 返回：

```http
400 Bad Request
invalid request body
```

## 5. 查询 Session 状态

### 请求

```http
GET /v1/sessions/{session_id}
```

### 响应 `200`

返回完整 Session 对象，至少使用以下字段：

```json
{
  "id": "ses_e2b13b6e62fe44008352e829",
  "status": "idle",
  "agent": {
    "id": "agt_23e6d8a187ac4382bf9665a9",
    "version": 4
  },
  "environment_id": "env_75ab58e00b7e4af1bf0153d9",
  "metadata": {},
  "updated_at": "2026-09-24T23:03:02.096+08:00"
}
```

页面恢复时先查询状态，再从事件分页加载内容。`idle`、`running` 等状态不能替代事件读取；前端应以最新事件和终态共同决定展示状态。

## 6. 查询 Session 事件

### 请求

```http
GET /v1/sessions/{session_id}/events?limit={limit}&page={page}
```

首次请求不传 `page`。服务端返回 `next_page` 时，下一次请求把它原样作为 `page` 参数。事件分页结果是稳定历史数据，适合刷新、重登录和 Open WebUI 重启后恢复。

### 响应 `200`

```json
{
  "data": [
    {
      "id": "evt_1d640444fc544df4a9f699b5",
      "type": "user.message",
      "session_id": "ses_fd75aa2265bf4fbd82942040",
      "sequence_number": 1,
      "processed_at": "2026-09-22T11:24:56.98+08:00",
      "content": [
        { "text": "Hello！", "type": "text" }
      ]
    },
    {
      "id": "evt_bdb2ebad27956ccc149d9eee",
      "type": "session.status_running",
      "session_id": "ses_fd75aa2265bf4fbd82942040",
      "sequence_number": 2,
      "processed_at": "2026-09-22T11:24:56.98+08:00"
    }
  ],
  "next_page": "evt_bdb2ebad27956ccc149d9eee"
}
```

已观察到的事件类型包括：

```text
user.message
session.status_running
agent.thinking
agent.message
agent.tool_use
agent.tool_result
session.status_idle
span.model_request_start
```

适配器必须保留完整事件对象，并按每个事件的 `id`、`sequence_number` 和 `type` 转发。每条 `agent.message` 都是独立消息，不能按正文合并。

空事件页的响应为：

```json
{
  "data": [],
  "next_page": null
}
```

## 7. 提交用户事件

### 请求

```http
POST /v1/sessions/{session_id}/events
```

请求体的顶层字段是 `events` 数组：

```json
{
  "events": [
    {
      "type": "user.message",
      "content": [
        { "text": "请开始执行任务", "type": "text" }
      ]
    }
  ]
}
```

### 响应 `200`

```json
{
  "data": [
    {
      "id": "evt_03e9c7a23c0948449e3afec4",
      "type": "user.message",
      "session_id": "ses_e2b13b6e62fe44008352e829",
      "sequence_number": 1,
      "processed_at": "2026-09-24T15:03:25.046661Z",
      "content": [
        { "text": "AgentAPI contract test", "type": "text" }
      ]
    }
  ]
}
```

提交成功只表示事件已被接收。Agent 的进度、工具调用和最终消息必须继续通过事件分页读取。一次请求可以包含多个事件，但 Open WebUI 第一版只提交一个 `user.message`。

目前尚未确认上游是否支持显式 `request_id` 幂等键。适配器在确认前不得对超时的提交盲目自动重试；应返回“结果未知”，由用户根据事件分页确认。

## 8. Session 文件列表

### 请求

```http
GET /v1/files?scope_id={session_id}&limit={limit}&page={page}
```

必须传 `scope_id`，并在 Open WebUI 后端先校验本地 Chat 权限。分页中的 `next_page` 原样作为下一次 `page` 参数。

### 响应 `200`

```json
{
  "data": [
    {
      "id": "file_400a1556d49846119828b254",
      "type": "file",
      "scope": {
        "id": "ses_fd75aa2265bf4fbd82942040",
        "type": "session"
      },
      "filename": "换命千金_前十集单集剧本/第10集_她手里的东西.md",
      "mime_type": "text/markdown; charset=utf-8",
      "size_bytes": 7520,
      "downloadable": true,
      "created_at": "2026-09-22T14:16:13.542+08:00"
    }
  ],
  "next_page": "page_file_400a1556d49846119828b254",
  "has_more": true,
  "first_id": "file_400a1556d49846119828b254",
  "last_id": "file_400a1556d49846119828b254"
}
```

Open WebUI 必须同时检查本地 `chat_id -> session_id` 绑定和每个返回文件的 `scope.type == 'session'`、`scope.id == session_id`，不能只凭用户传入的 `file_id` 下载。

## 9. 读取 Session 文件内容

### 请求

```http
GET /v1/files/{file_id}/content
```

文件接口没有 `scope_id` 参数，因此调用前必须完成本地会话权限校验，并确认文件列表中的 scope 属于当前 Session。

### 响应

成功时返回文件字节流，不是 JSON。例如已验证的 Markdown 文件响应头：

```http
200 OK
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename="换命千金_前十集单集剧本/第10集_她手里的东西.md"
```

响应字节数与文件列表中的 `size_bytes` 一致。Open WebUI 直接流式代理响应，不写本地 `file` 表、不写本地文件，也不上传对象存储。Range/断点下载尚未单独验证，第一版不依赖该能力。

## 10. 删除 Session

### 请求

```http
DELETE /v1/sessions/{session_id}
```

### 响应

成功返回：

```http
204 No Content
```

Open WebUI 删除 Agent 会话时先调用上游删除。上游删除成功后再删除本地 `chat` 索引；上游失败则保留本地绑定并允许重试。Session 文件随上游 Session 生命周期处理。

## 11. 尚未纳入第一版契约的能力

以下能力不是当前轻量方案的必需依赖，不能在未验证前作为实现前提：

- 取消或中断正在运行的任务；
- 显式消息 `request_id` 幂等和重复提交语义；
- 文件 Range/分块读取；
- Agent、Environment、Skill 的发现和下拉列表接口；
- 上游主动 SSE/Realtime 通道。

第一版不依赖这些能力：Agent 配置保存管理员填写的 ID、页面使用事件分页轮询、超时提交不自动重试。若后续确认上游接口，再作为兼容增强加入适配器。

## 12. Open WebUI 适配器映射

| Open WebUI 业务动作 | 上游接口 |
| --- | --- |
| 创建 Agent 会话 | `POST /v1/sessions` |
| 刷新会话状态 | `GET /v1/sessions/{session_id}` |
| 加载历史和执行进度 | `GET /v1/sessions/{session_id}/events` |
| 发送用户消息 | `POST /v1/sessions/{session_id}/events` |
| 列出会话文件 | `GET /v1/files?scope_id={session_id}` |
| 预览或下载文件 | `GET /v1/files/{file_id}/content` |
| 删除 Agent 会话 | `DELETE /v1/sessions/{session_id}` |

所有上游请求都必须在后端完成权限校验、Session 绑定校验、超时设置和错误转换。AgentAPI 返回的消息、状态和文件不在 Open WebUI 本地持久化。
