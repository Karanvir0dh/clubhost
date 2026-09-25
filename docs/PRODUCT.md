# ClubHost

A deployment portal for student clubs that need to host small websites and services without operating Kubernetes themselves.

## User workflow

An administrator signs in with their club's access token, chooses an approved application template, names the application, and requests a deployment. The portal tracks provisioning and readiness. A later release creates a new revision; an unsuccessful rollout remains visible and the administrator can restore the last successful revision.

## Scope

- Two seeded clubs and two approved templates: a club landing page and an event information page.
- Authenticated API with club-scoped authorization.
- Application inventory, deployment history, and explicit rollback.
- Durable application and revision records.
- A local simulation mode, clearly labeled, for development without a cluster.
- A Kubernetes adapter with manifest generation and readiness observation. Real cluster deployment has not been validated.

Arbitrary repository builds, email delivery, custom domains, secret editing, and billing are not supported. The dashboard reports release states; it does not collect uptime or traffic metrics.

## Content and events

Club administrators can edit a draft, preview it, and deploy an immutable content snapshot. Each application has an explicitly enabled public portal URL. Rollback restores the old snapshot while preserving the current draft.

Event pages collect names and emails into a transactional capacity-limited registration list with a FIFO waitlist. Organizers can check in confirmed attendees, cancel registrations, and export a CSV. Cancelling a place or increasing live capacity promotes waiting registrations. Public forms disclose that no confirmation email is sent. One event-page application represents one event.

The public portal renders the latest successful release in both infrastructure modes. In Kubernetes mode the deployed static page contains the same snapshot and can link back to the portal for registration. Public-page visibility does not control direct cluster networking.

Draft conflict checks, additive database migration, duplicate registration handling, private attendee lists, and preservation of registrations across content rollback are implemented. Individual accounts, verification emails, and public hosting are not configured.

## Architecture

Browser -> portal API -> database

The deployment service authorizes the request, validates the template against a server-side catalog, records a revision, and delegates provisioning to an infrastructure adapter. The adapter reports observed state; submitting resources alone does not mean a deployment is ready.

The application uses a single API process. Its reconciliation loop retries transient infrastructure errors and recovers pending releases from the database after restart. Multiple API workers require a distributed lease mechanism that is not yet implemented.

## Correctness requirements

1. The club access token is checked for every administrative application read or mutation.
2. A club cannot access another club's applications by guessing identifiers.
3. Templates and versions come from the approved catalog, never an arbitrary image supplied by the browser.
4. Each release has a monotonically increasing revision number.
5. Only one operation may be active for an application at a time.
6. Rollback creates a new revision referencing a previous successful release; history is retained.
7. A failed release does not replace the last successful revision.
8. User-visible status distinguishes simulated infrastructure from actual cluster observations.

## Kubernetes deployment

Use a namespace per club, resource quotas, restricted pod settings, and network policies supported by the cluster's network implementation. Namespace separation alone is not a complete security boundary.

Use approved container images, resource requests and limits, readiness probes, Deployments, and Services. Initially expose demonstration applications using explicit local port forwarding. Add ingress and TLS after domain and cluster configuration are chosen.

Observe deployment generation, available replicas, and progress-deadline failures. Kubernetes reports a stalled rollout; the portal must implement its own rollback policy rather than assuming Kubernetes rolls back automatically.

## Validation

- Local API tests cover cross-club access, concurrent requests, content snapshots, event capacity, failure recovery, and rollback history.
- A browser smoke test exercises the event workflow with isolated test data.
- GitHub Actions runs lint and API tests on pushes and pull requests.
- Real-cluster rollout, failed-update recovery, load tests, and backup restoration still need validation.

## References

- https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
- https://kubernetes.io/docs/concepts/security/multi-tenancy/
