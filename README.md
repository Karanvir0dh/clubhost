# ClubHost

A student-club deployment portal built with Python/FastAPI. Club administrators create websites and event pages, edit their content, deploy versioned releases, and manage event registrations.

**Status:** working local product workflow. Content editing, public portal pages, registrations, waitlists, and attendee management are real database-backed features. The default simulator simulates only infrastructure rollouts. A kubectl adapter is included for explicitly configured development clusters; cluster operation remains unverified. Public internet hosting, individual user accounts, and email delivery are not configured.

## Run locally (PowerShell)

```powershell
cd clubhost
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m uvicorn clubhost.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. On first startup, the portal generates two unique local access tokens in `.data/tokens.json`. Open that file locally and paste the `computing` token into the sign-in form. The other token opens a separate Outdoor Adventure Club workspace. Keep this file private; it is ignored by Git. The browser retains your token in memory only, so refreshing the page requires signing in again.

Use one server process / worker. Do not use `--workers` or run two instances against the same database. The reconciliation loop runs in the API process and does not use distributed worker leases.

## Try the workflow

1. Select **Event page**, enter a unique application name, and create it.
2. Open **Edit content**. Add a title, description, contact email, future event date, location, and capacity. Enable **Accept registrations**.
3. Save the draft. **Preview draft** shows saved changes without affecting the live page.
4. Select **Deploy saved draft** and wait for readiness (about five seconds in simulator mode).
5. Select **Enable public page**, then **Open public page**. The URL is `/sites/{club}/{application-name}` and visitors do not need an access token.
6. Submit a name and email from that page. Requests fill available places, then join the waitlist.
7. Return to **Manage attendees** to check in confirmed attendees, cancel places, or download a CSV. Cancelling a confirmed attendee promotes the oldest waiting request if the event has not started.
8. Edit and deploy another version. Under **Release history and troubleshooting**, restore a previous successful revision to restore its content and theme. Your current draft remains intact.

For a club website, use the same workflow without registration fields. Content supports plain text, a contact email, and one HTTP(S) link. HTML is escaped; there is no arbitrary script or image upload.

## Product behavior

- **Drafts versus releases:** each release captures a content snapshot. Saving while a release is pending does not modify that release. A failed release leaves the last successful content live. Concurrent draft edits detect conflicts instead of overwriting each other.
- **Visibility:** pages start private. Disabling public access returns 404 for the portal page and stops new registrations. It does not erase registrations, recall downloaded pages, or change direct access to a Kubernetes Service.
- **Capacity:** database transactions prevent concurrent registrations from overbooking. Increasing live capacity promotes the oldest waiting requests. Lowering capacity or rolling back preserves already confirmed attendees; no new places are assigned until there is room.
- **Registration lifecycle:** each event-page application represents one event; create a new application for a different event. A normalized email has one registration per event. Duplicate requests do not reveal whether that address is registered and do not reactivate cancelled places. Registration closes at the published event time. Draft changes to date or capacity take effect only after deployment.
- **Privacy:** public pages expose no attendee list. Attendee data, check-in, cancellation, and CSV export require the owning club's token. CSV cells are escaped against spreadsheet formula injection.
- **Abuse controls:** registration attempts are limited to ten per minute per client IP in this single-process server, plus a hidden bot-trap field. This is a basic local safeguard, not email verification. Shared campus IPs share a limit. Only trust forwarded client IPs from your own configured reverse proxy.
- **Notifications:** no email is sent. Organizers must contact attendees themselves to confirm places, promotions, changes, or cancellations. The public form states this explicitly.

With the default loopback binding, public URLs are only reachable on your computer. Making the service reachable to a real club requires hosting, HTTPS, individual accounts, and appropriate operational setup. No seeded clubs or smoke-test attendees represent real customers.

Existing databases migrate additively on startup. Existing applications remain private, and existing releases receive default starter content while retaining their identifiers and history. Stop older server processes before upgrading; keep normal database backups.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Tests cover authorization, cross-club access, quotas, concurrent deployments and registrations, content snapshots, draft conflicts, waitlist promotion, attendee operations, export, rate limits, old-database migration, failure recovery, and Kubernetes readiness interpretation using mocked responses. They do not establish that a real cluster rollout works.

Pytest uses a fresh `.test-runs/run-...` directory per invocation and disables its shared cache to avoid Windows file-permission conflicts between runs. Test artifacts remain there for inspection and are ignored by Git. The tests do not use your normal `.data` database. An explicit `--basetemp` overrides this default; only point it at disposable test data because pytest clears that directory.

The optional browser smoke test uses an isolated temporary database and installed Microsoft Edge. It exercises editing, publication, visitor registration, waitlisting, check-in, CSV download, cancellation, and a mobile viewport:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.lock
.\.venv\Scripts\python.exe scripts/browser_smoke.py
```

It creates desktop/mobile screenshots under `.data` and removes its temporary test database. It does not modify your normal workspace data.

## Docker simulator

```powershell
docker compose up --build
```

The portal listens on localhost:8000. Its private tokens are in `/data/tokens.json` inside the container. Read that file locally with `docker compose exec portal cat /data/tokens.json`. The named volume preserves releases across container restarts. The supplied Docker image runs simulator mode and does not include kubectl or a kubeconfig.

## Kubernetes development mode

Use a disposable local cluster such as kind or Docker Desktop Kubernetes with kubectl installed on the host. The adapter requires an explicit context and never selects the current context implicitly. It does not create namespaces automatically.

Generate the namespace, quota, and network-policy manifests for review:

```powershell
.\.venv\Scripts\python.exe scripts/bootstrap_cluster.py | Out-File -Encoding utf8 bootstrap.json
```

After reviewing the file, apply it to your chosen development cluster:

```powershell
kubectl --context YOUR_CONTEXT apply -f bootstrap.json
$env:CLUBHOST_MODE = 'kubernetes'
$env:CLUBHOST_KUBE_CONTEXT = 'YOUR_CONTEXT'
$env:CLUBHOST_DATA = '.data/kubernetes'
.\.venv\Scripts\python.exe -m uvicorn clubhost.main:create_app --factory --host 127.0.0.1 --port 8000
```

Sign in with the tokens generated in `.data/kubernetes/tokens.json`. A deployment creates a versioned ConfigMap containing the release's content, an nginx Deployment, and a ClusterIP Service in the club namespace. The application details show the port-forward command. Start it in another terminal and open http://localhost:8081.

Event registration is hosted by the FastAPI portal, not the static nginx container. Set `CLUBHOST_PUBLIC_BASE_URL` to the visitor-accessible portal URL before starting Kubernetes mode (for example `https://clubs.example.edu`). Generated event pages will link there for registration. Public access must also be enabled in the portal. Without this setting, the cluster copy has no registration link; the portal page still works locally. Updating this setting requires a new release to update existing cluster pages.

Networking defaults to deny both ingress and egress. Enforcement requires a network-policy-capable CNI. Direct port forwarding is intended for local operator testing; no ingress controller or public domain is configured. Before adding ingress, explicitly allow only the ingress controller's traffic to port 8080.

The configured kubeconfig supplies the adapter's privileges. For this local prototype an operator prepares the namespaces; production requires a dedicated identity and narrowly scoped RBAC. The web server must not be exposed with an administrator kubeconfig. Containers do not receive service-account tokens, run as non-root, drop capabilities, and have resource limits. Namespaces alone are not a complete tenant security boundary.

The last-successful revision shown in the portal is release history, not continuous uptime monitoring. Out-of-band cluster changes, node failures, and partial rollouts may affect actual availability. A failed rollout can leave resources in the cluster; inspect them and explicitly restore a successful revision. Old revision ConfigMaps are retained; automated garbage collection is not implemented.

## Project layout

```text
clubhost/        FastAPI API, persistence, provisioning adapters, browser UI
tests/           Workflow and isolation tests
scripts/         Reviewable cluster-bootstrap manifest generator
docs/            Product boundaries and architecture
.github/         CI checks
```

The UI is plain HTML/CSS/JavaScript, served by FastAPI. SQLite transactions enforce release serialization, quotas, and event capacity. Planned additions include PostgreSQL, OIDC club membership, worker leases, audit identities, application deletion, secret management, ingress/TLS, email verification/notifications, and tested backup restoration.

## Validation status

The local API suite covers 20 tests. The browser smoke test covers the complete event workflow and a mobile viewport. Real Kubernetes rollout and rollback, load testing, and backup restoration still require validation. No production availability or performance measurements are available.

Technical references: [Kubernetes Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/), [multi-tenancy](https://kubernetes.io/docs/concepts/security/multi-tenancy/), [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
