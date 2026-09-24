# Agent Relay

Agent Relay is a small FastAPI service for registering agents, delivering one
task at a time, and recording results. PostgreSQL persists the queue and
attempts, while workers execute tasks on their own machines. The included
worker deterministically returns `input.upper()`.

## Run it

```bash
docker compose up --build
```

Open <http://127.0.0.1:8000/> for the token-based local dashboard. The default
Compose network connects the API to the `postgres` service and persists data in
the `postgres-data` volume. `GET /health` is a liveness check and `GET /ready`
verifies database connectivity and schema (it queries the real tables, so a
missing schema reports not-ready instead of passing with zero tables).

## Run it in kind

Build and load the application image, then apply the Kubernetes manifests:

```bash
docker build --platform linux/amd64 --provenance=false --sbom=false \
  -t agent-relay:local .
kind create cluster --name agent-relay
kind load docker-image agent-relay:local --name agent-relay
kubectl apply -k k8s
kubectl wait --for=condition=Ready pods --all -n agent-relay --timeout=180s
kubectl port-forward -n agent-relay service/agent-relay 8000:8000
```

The `postgres` StatefulSet stores its database on a 1 Gi persistent volume.
The Agent Relay Deployment waits for PostgreSQL and exposes `/ready` and
`/health` probes before receiving Service traffic.

## Run CI locally with act

With Docker, kind, kubectl, and `act` installed, run the same test, build, and
deployment workflow locally:

```bash
act push \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-options '--network host' \
  --var DEPLOY_TO_KIND=true \
  --var KIND_CLUSTER_NAME=agent-relay
```

`act` mounts the Docker socket automatically. Host networking lets the job use
the kind API endpoint exported on `127.0.0.1`. The workflow runs the starter
tests and the real HTTP integration test against PostgreSQL first, then builds
an immutable tag from the commit and workflow run, loads it into kind, and
waits for the Deployment rollout. In GitHub Actions, configure a self-hosted
runner with Docker and kind access and set the repository variable
`DEPLOY_TO_KIND=true`; hosted runners cannot reach a kind cluster on your
machine.

Register two identities and send a task:

```bash
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"uppercase"}')
```

The response contains each agent's secret `token` once. Keep it outside source
control. Use `Authorization: Bearer <token>` for all subsequent API calls;
registration is the only unauthenticated endpoint. For a shared installation,
set `RELAY_ENROLLMENT_SECRET` and send it as `X-Enrollment-Secret` when
registering.

## Run the deterministic worker

The worker can register itself and save credentials in a mode-0600 JSON file:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1
```

For failure/redelivery demonstrations, make local execution intentionally slow
and stop the process after one completion:

```bash
uv run python main.py worker --credentials ./uppercase-credentials.json \
  --slow-seconds 75 --worker-id slow-laptop
```

The worker heartbeats during long work. Killing it leaves the claim leased;
after the 60-second lease expires, another worker can claim the task with a new
token and incremented attempt number. `RELAY_LEASE_SECONDS` and
`RELAY_MAX_ATTEMPTS` are configurable server settings.

An existing credential can also be supplied explicitly (the token is not
written to disk):

```bash
uv run python main.py worker --agent-id agent_123 --token agt_… --worker-id laptop-2
```

## Storage and delivery behavior

`database.py` contains the SQLAlchemy models and transaction setup. `storage.py`
contains task, claim, and recovery operations; routes and request models are in
`main.py` and `schemas.py`. PostgreSQL transactions and
`FOR UPDATE SKIP LOCKED` make concurrent claims safe across API and worker
processes. SQLite remains available as the isolated test backend.

Claims are at-least-once and leased for 60 seconds by default. Heartbeats extend
an active lease. A completion or failure must include the recipient's bearer
token and claim token. Repeating the exact terminal request with that claim
token is idempotent; a stale token or different result receives `409`.

## Verify

The test suite covers the main protocol, sender/recipient access boundaries,
hashed claim-token behavior, idempotent terminal retries, concurrent claims,
lease expiry before and after recovery, pagination/error shape, and dashboard
asset serving:

```bash
uv run pytest -q

RELAY_INTEGRATION_BASE_URL=http://127.0.0.1:8000 \
  uv run pytest -q test_compose_integration.py
```

Tests default to a scratch database at `/tmp/agent-relay-test.db` so they
do not alter the Compose database. The fixture drops and recreates all tables
on whatever `RELAY_DATABASE_URL` points at, so never point the unit suite at a
database containing data you need. The Compose integration test uses only the
public HTTP API and is safe to rerun against the development stack.
