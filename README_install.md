# 安装与运行指南（yarn 版）

## 前置依赖

安装以下软件并确保在终端中可用（添加到 PATH）：

| 依赖 | 最低版本 | 说明 |
| ---- | -------- | ---- |
| Node.js | 22.x | [官网下载](https://nodejs.org)，安装时勾选 "Add to PATH" |
| yarn | 4.5.x | Node 内置，执行 `corepack enable` 即可启用（无需 npm 全局安装） |
| Python | 3.12 | [官网下载](https://python.org)，安装时勾选 "Add Python to PATH" |
| uv | 最新版 | Python 包管理器，PowerShell 执行：<br>`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

验证安装（重新打开终端后执行）：

```bash
node --version   # v22.x.x
yarn --version   # 4.x.x
python --version # 3.12.x
uv --version     # uv 版本号
```

> 💡 **首次启用 yarn**：Node.js 22 已内置 Corepack，直接执行 `corepack enable` 即可激活与 `packageManager` 字段匹配的 yarn 版本。

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/CopilotKit/OpenIntelligentUI.git
cd OpenIntelligentUI
```

### 2. 启用 Corepack 并安装依赖

```bash
corepack enable
yarn install
```

该命令会同时安装以下 workspace 包的全部依赖：
- `apps/app` — Next.js 前端
- `apps/agent` — Python Agent（其 Python 依赖在首次启动时由 `uv sync` 安装）
- `apps/mcp` — 可选的 MCP 服务
- `packages/design-system` — 共享样式包

### 3. 配置 Agent 环境变量

创建 `apps/agent/.env` 文件并填入 API Key：

```dotenv
# ====== 对话模型（二选一）======
# 方案 A：使用 OpenAI（默认 LLM_MODEL=chat-latest 走 OpenAI）
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=chat-latest

# 方案 B：使用 Anthropic Claude
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
# LLM_MODEL=claude-3-5-sonnet-latest

# ====== Jev 可视化路由（推荐，否则无法生成图表/地图/3D）======
TYPESAFE_API_KEY=ts-xxxxxxxxxxxx
JEV_MODEL=jev-latest
```

> 💡 **不填密钥也能启动**：聊天界面会要求用户在浏览器中临时输入密钥（BYOK 模式），密钥仅保留在当前会话内存中，刷新即清除。

### 4. （可选）配置前端环境变量

创建 `apps/app/.env.local`，默认值通常无需修改：

```dotenv
# Agent 后端地址
LANGGRAPH_DEPLOYMENT_URL=http://localhost:8123

# 如需启用独立 MCP 服务则取消注释
# MCP_SERVER_URL=http://localhost:3100/mcp

# 限流（默认关闭）
# RATE_LIMIT_ENABLED=false
# RATE_LIMIT_WINDOW_MS=60000
# RATE_LIMIT_MAX=40
```

## 启动运行

### 一键启动全部服务（推荐）

```bash
yarn dev
```

该命令使用 `concurrently` 并行启动：
- **Turbo** — 启动 Next.js 前端（端口 3000）和 MCP 服务（端口 3100）
- **Agent** — 进入 `apps/agent` 执行 `uv sync` 安装 Python 依赖，然后启动 uvicorn（端口 8123）

启动后访问：
- 聊天界面：http://localhost:3000
- 前端健康检查：http://localhost:3000/api/health → `{"status":"ok"}`
- Agent 健康检查：http://localhost:8123/health → `{"status":"ok"}`

### 分别启动（便于调试）

**终端 1 — 前端**：
```bash
yarn dev:app
```

**终端 2 — Agent**：
```bash
cd apps/agent
uv sync                                  # 首次或依赖变更时执行
uv run uvicorn main:app --host 0.0.0.0 --port 8123 --reload
```

**终端 3 — MCP 服务（可选）**：
```bash
yarn dev:mcp
```

## 其他常用命令

| 功能 | 命令 |
| ---- | ---- |
| 构建生产包 | `yarn build` |
| 运行 JS 测试 | `yarn test` |
| 运行 Python 测试 | `cd apps/agent && uv run pytest` |
| Lint 检查 | `yarn lint` |
| 清理构建产物 | `yarn clean` |
| 仅运行前端构建 | `yarn workspace @repo/app build` |
| 仅运行单个包的命令 | `yarn workspace <包名> <命令>` |

## 常见问题

### 端口被占用
```bash
netstat -ano | findstr :3000
netstat -ano | findstr :8123
taskkill /F /PID <PID>
```

### 依赖下载慢 / 超时（需走代理或镜像）

**方案 A：设置代理**（终端内临时生效）
```bash
# Windows PowerShell
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
$env:http_proxy="http://127.0.0.1:7897"
$env:https_proxy="http://127.0.0.1:7897"
```

**方案 B：设置国内镜像**
```bash
yarn config set registry https://registry.npmmirror.com
```

### yarn 版本不匹配（packageManager 字段）

```bash
corepack enable
corepack prepare yarn@4.5.0 --activate
```

### PowerShell 脚本执行被拦截（yarn.ps1 被策略拦截）

改用 `.cmd` 后缀或直接调用 Corepack：
```bash
yarn.cmd install
yarn.cmd dev
# 或
corepack yarn install
corepack yarn dev
```

### workspace 依赖未生效（`workspace:*`）

Yarn 4 默认支持 `workspace:` 协议。如果 `@repo/design-system` 出现 `Cannot resolve`，确认：
1. 根目录 `package.json` 的 `workspaces` 数组包含 `packages/*`
2. 执行 `yarn install` 时没有报错
3. 必要时运行 `yarn workspaces list` 查看所有被识别的 workspace 包
