# Environment & Real-Tenant Configuration

Docker Compose orchestration, API contracts, and integration/E2E test suite for the Anything-But-NET Enterprise Copilot stack.

## Services & Ports

| Service | Host Port | Container Port | Description |
| :--- | :--- | :--- | :--- |
| `net-service` | `5224` | `8080` | Slack Bot API & SharePoint/Entra Ingestion Worker |
| `java-service` | `8080` | `8080` | Core orchestration, ACL storage, diagnostics |
| `python-service` | `8000` | `8000` | RAG retrieval & grounded LLM generation |
| `webui-service` | `3000` | `3000` | Web dashboard / UI |
| `mock-graph` | `8088` | `8080` | Local Microsoft Graph mock server |
| `db` (`pgvector`) | `5432` | `5432` | PostgreSQL + pgvector database |
| `kafka` | `9092` | `9092` / `9094` | Event bus (`9094` internal Docker listener) |

---

## Secrets & Credentials Policy

1. **No secrets in Git**:
   - `.env` and `docker-compose.override.yml` are explicitly ignored in `.gitignore`.
   - Default configuration files (`appsettings.json`, `.env.example`, `docker-compose.yml`) contain only placeholders or empty strings (`""`) for secrets.
2. **No secrets in logs**:
   - `SlackController` logs only HTTP status codes and Slack error codes (`SlackError`), never bot tokens or full API response bodies.
   - `GraphClientFactory` enforces **HTTPS-only** endpoints whenever real Azure Entra ID credentials (`ClientSecretCredential`) are configured. Any non-HTTPS URL (such as `http://mock-graph:8080/v1.0`) is automatically ignored so a real Azure Bearer token is never transmitted over plain HTTP or sent to a mock server.
3. **Fail-closed ACLs**:
   - `MockSharePointAdapter` and `DocumentIngestionWorker` enforce deny-by-default permissions: documents without resolvable ACLs are skipped rather than falling back to `"all-employees"`.

---

## Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `DB_USER` | No | `postgres` | PostgreSQL username |
| `DB_PASSWORD` | No | `example` | PostgreSQL password |
| `DB_NAME` | No | `copilot_db` | PostgreSQL database name |
| `LLM_API_KEY` | For live RAG | `""` | API key for the LLM provider (`python-service`) |
| `SLACK_TOKEN` | For Slack bot | `""` | Slack Bot User OAuth Token (`xoxb-...`) |
| `GRAPH_BASE_URL` | No | `http://mock-graph:8080/v1.0` | Graph API base URL (`https://graph.microsoft.com/v1.0` for real tenant) |
| `GRAPH_TENANT_ID` | Real tenant only | `""` | Microsoft Entra ID Tenant (Directory) ID |
| `GRAPH_CLIENT_ID` | Real tenant only | `""` | Microsoft Entra ID App Registration (Client) ID |
| `GRAPH_CLIENT_SECRET` | Real tenant only | `""` | Microsoft Entra ID Client Secret value |
| `TEST_USER_EMAIL` | No | `alice@contoso-mock.onmicrosoft.com` | User email used by E2E chat tests (`tests/api-tests/e2e/test_chat_error_retry.py`) |

---

## Switching Between Mock Graph and Real Tenant

### Mode 1: Local Mock Graph (Default / CI)
Leave `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, and `GRAPH_CLIENT_SECRET` empty:
```bash
cp .env.example .env
docker compose up -d
```
`net-service` automatically falls back to `StaticTokenProvider` and `http://mock-graph:8080/v1.0`.

### Mode 2: Real Microsoft Entra ID & SharePoint Tenant
1. Create a local `.env` (or `docker-compose.override.yml`) with your real credentials — **do not commit either file**:
   ```dotenv
   GRAPH_BASE_URL=https://graph.microsoft.com/v1.0
   GRAPH_TENANT_ID=<tenant-guid>
   GRAPH_CLIENT_ID=<client-guid>
   GRAPH_CLIENT_SECRET=<client-secret>
   SLACK_TOKEN=<slack-bot-token>
   TEST_USER_EMAIL=<real-tenant-user-email>
   ```
2. Restart `net-service`:
   ```bash
   docker compose up -d net-service
   docker compose logs -f net-service
   ```
3. Verify sync output in logs (real Entra users/groups and SharePoint documents published to Kafka without printing credentials).

---

## Approved Least-Privilege Scopes

### Microsoft Graph (Application Permissions — Read-Only)
The Entra ID App Registration used by `net-service` must be restricted to **read-only** scopes only (no `*.ReadWrite.All`):

| Scope | Used By | Purpose |
| :--- | :--- | :--- |
| `User.Read.All` | `GraphUserAdapter`, `MockSharePointAdapter` | List tenant users and resolve user emails for direct file permissions |
| `Group.Read.All` | `GraphUserAdapter`, `MockSharePointAdapter` | Read `transitiveMemberOf` groups, resolve M365 group `displayName` and `owners` |
| `Sites.Read.All` | `MockSharePointAdapter` | Discover SharePoint sites and drives (`$select=id,name,owner`) |
| `Files.Read.All` | `MockSharePointAdapter` | Read drive delta changes, file contents, and item `/permissions` |

### Slack Bot Token Scopes
| Scope | Used By | Purpose |
| :--- | :--- | :--- |
| `app_mentions:read` / `channels:history` / `im:history` | `SlackController` | Receive question events from channels/DMs |
| `users:read`, `users:read.email` | `SlackController` | Resolve Slack user ID to corporate email (`users.info`) |
| `chat:write` | `SlackController` | Post `"Thinking..."` acknowledgement (`chat.postMessage`) and replace it with the answer/fallback (`chat.update`) |

---

## Running Verification Tests

### Contract & E2E Tests (`environment/tests/api-tests`)
```bash
cd tests/api-tests
uv sync

# Schema & contract tests (no secrets required)
uv run pytest

# E2E tests against running Docker Compose stack
uv run pytest -m e2e -v
```
