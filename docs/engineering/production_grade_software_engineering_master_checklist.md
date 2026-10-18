# The Production-Grade Software Engineering Master Checklist

This is the stuff a vibe coder can miss while building an application
that looks incredible in a demo but falls apart under real traffic,
malicious attacks, database failures, deployments, or unexpected costs.

If your target is software capable of serving **millions of concurrent
users**, you need to think beyond frontend, backend, database, and
deployment. You need to engineer the entire system: infrastructure,
reliability, security, data integrity, performance, operations, and
recovery.

One important distinction: **millions of registered users, millions of
daily active users, and millions of simultaneous connections are three
completely different engineering problems.** No checklist or technology
stack guarantees that scale. Architecture, workload, capacity testing,
and operational execution determine whether you can achieve it.

This checklist is deliberately broad. Use it as a master audit for any
serious web application, including an AI-powered platform.

## 1. Software architecture and system design

*Foundation · Every production application*

-   [ ] Define functional and non-functional requirements.
-   [ ] Establish explicit SLOs, SLIs, and SLAs where applicable.
-   [ ] Set measurable latency targets: p50, p95, p99.
-   [ ] Define availability targets and error budgets.
-   [ ] Estimate requests per second, concurrent connections, data
    volume, and growth.
-   [ ] Create architecture diagrams and data-flow diagrams.
-   [ ] Perform threat modeling before implementation.
-   [ ] Establish clear module boundaries and ownership.
-   [ ] Separate API-serving workloads from background processing.
-   [ ] Make application servers stateless wherever possible.
-   [ ] Avoid circular dependencies and tightly coupled services.
-   [ ] Define API contracts and schemas.
-   [ ] Establish versioning and backward-compatibility rules.
-   [ ] Define synchronous versus asynchronous workflows.
-   [ ] Design for graceful degradation when dependencies fail.
-   [ ] Establish failure isolation and blast-radius boundaries.
-   [ ] Avoid premature microservices; consider a modular monolith
    first.
-   [ ] Document architecture decisions using ADRs.
-   [ ] Define capacity limits and resource budgets for each component.

**Common miss:** splitting an application into microservices before
establishing module boundaries, observability, deployment automation,
and distributed failure handling.

## 2. Repository, code quality, and development workflow

-   [ ] Consistent repository structure and naming conventions.
-   [ ] Separate development, test, staging, and production
    configurations.
-   [ ] Strict type checking where supported.
-   [ ] Linting and automated formatting.
-   [ ] Static analysis and code-quality checks.
-   [ ] Explicit error handling; no silent failures.
-   [ ] Centralized configuration management.
-   [ ] Environment-variable validation at startup.
-   [ ] No hardcoded URLs, credentials, keys, or environment-specific
    assumptions.
-   [ ] Dependency and package version pinning.
-   [ ] Lockfiles committed to version control.
-   [ ] Dependency upgrade policy.
-   [ ] Reusable modules instead of duplicated business logic.
-   [ ] Consistent exception and error-response formats.
-   [ ] No debug endpoints or development bypasses in production.
-   [ ] Code ownership and mandatory peer review.
-   [ ] Branch protection and protected release branches.
-   [ ] Clear contribution and onboarding documentation.

## 3. CI/CD and release engineering

*Continuous integration · Continuous delivery · Rollback safety*

1.  **Developer pushes code** --- branch protection, pull request,
    review.
2.  **Automated quality gates** --- lint, types, unit tests, integration
    tests, SAST, dependency scans.
3.  **Build and verify artifact** --- container build, SBOM, image scan,
    artifact signing.
4.  **Deploy to staging** --- migrations, smoke tests, E2E, security
    checks.
5.  **Controlled production release** --- canary or blue-green, health
    checks, telemetry, automatic rollback gates.

Checklist:

-   [ ] Automated CI on every pull request.
-   [ ] Unit, integration, contract, and end-to-end tests.
-   [ ] Database migration validation.
-   [ ] Security and license policy checks.
-   [ ] Build artifacts once and promote the same artifact across
    environments.
-   [ ] Immutable, versioned container images.
-   [ ] Infrastructure changes reviewed through IaC plans.
-   [ ] Deployment approvals for sensitive production environments.
-   [ ] Short-lived deployment credentials using workload identity or
    OIDC.
-   [ ] Canary, rolling, or blue-green deployment strategy.
-   [ ] Readiness, liveness, and startup probes.
-   [ ] Automated rollback based on health and error metrics.
-   [ ] Feature flags and emergency kill switches.
-   [ ] Expand-and-contract database migrations for zero-downtime
    releases.
-   [ ] Backward-compatible APIs during rolling deployments.
-   [ ] Deployment logs, audit trail, release metadata, and commit
    linkage.
-   [ ] Rollback procedures that account for database changes and data
    compatibility.
-   [ ] Scheduled dependency updates and security patches.
-   [ ] CI pipeline isolation so untrusted pull requests cannot access
    production secrets.
-   [ ] Recovery procedure for compromised build pipelines.

**Tools to evaluate:** GitHub Actions, GitLab CI/CD, Docker, an
artifact/container registry, Terraform or OpenTofu, and a deployment
platform suited to your scale.

CI/CD is not just automated deployment. It is a controlled, repeatable,
auditable path from source code to production.

## 4. Scalability and high-concurrency engineering

This is where a prototype usually meets its first serious architectural
limit.

-   [ ] Horizontal scaling of API instances.
-   [ ] Load balancing with health-aware routing.
-   [ ] Autoscaling based on CPU, memory, request rate, queue depth, and
    latency.
-   [ ] Maximum and minimum replica limits.
-   [ ] Connection draining during deployment and scale-down.
-   [ ] Backpressure when the system cannot process incoming work.
-   [ ] Bounded queues to prevent unbounded memory growth.
-   [ ] Request deadlines and end-to-end timeouts.
-   [ ] Concurrency limits per endpoint and dependency.
-   [ ] Bulkheads to isolate resource-intensive workloads.
-   [ ] Circuit breakers for failing downstream services.
-   [ ] Retries with exponential backoff and jitter.
-   [ ] Retry budgets to prevent retry storms.
-   [ ] Idempotency keys for retryable write operations.
-   [ ] Admission control and load shedding under overload.
-   [ ] Graceful degradation for nonessential features.
-   [ ] Separate capacity pools for CPU-intensive and memory-intensive
    work.
-   [ ] Avoid shared in-memory state across replicas.
-   [ ] Account for connection limits in databases, caches, and external
    APIs.
-   [ ] Capacity planning for traffic spikes and sudden viral growth.
-   [ ] Multi-region architecture when justified by availability and
    latency requirements.
-   [ ] Regional failover and traffic-shifting procedures.
-   [ ] Distributed coordination only when genuinely necessary.

**Critical detail:** 100,000 requests per second with small responses
can be easier than 10,000 requests per second where every request
triggers expensive model inference, multiple database queries, and
external API calls.

## 5. Caching --- at every appropriate layer

### Layer 1 --- Edge/CDN cache

Static assets, public pages, cacheable API responses, geographically
distributed delivery.

### Layer 2 --- Application cache

Frequently reused computations, configuration, metadata and expensive
derived results.

### Layer 3 --- Distributed cache

Redis or equivalent for shared cache entries, carefully designed session
storage, rate-limit counters and coordination.

### Layer 4 --- Database and storage

Query plans, indexes, buffer pools, object-storage caching and
precomputed aggregates.

-   [ ] Define what is cacheable and what must always be read from the
    source of truth.
-   [ ] Choose cache-aside, read-through, write-through, or write-behind
    deliberately.
-   [ ] Define TTLs and expiration policies.
-   [ ] Use explicit invalidation rules.
-   [ ] Prevent stale data from persisting after updates.
-   [ ] Prevent cache stampedes using request coalescing, locks, or
    controlled refreshes.
-   [ ] Mitigate cache penetration from repeated nonexistent keys.
-   [ ] Use TTL jitter to avoid synchronized expiration.
-   [ ] Prevent unbounded cache growth and memory exhaustion.
-   [ ] Establish eviction policies.
-   [ ] Monitor hit rate, misses, latency, memory, evictions and
    connection saturation.
-   [ ] Handle cache outages without taking down the entire application.
-   [ ] Prevent cache poisoning and untrusted cache-key manipulation.
-   [ ] Include tenant and authorization scope in cache keys where
    needed.
-   [ ] Never serve one user's private data to another user through an
    improperly scoped cache.
-   [ ] Ensure cached HTTP responses respect `Cache-Control`, `Vary`,
    and privacy requirements.
-   [ ] Protect cached objects from unsafe deserialization.
-   [ ] Plan for cache warm-up after restart or failover.

**Common miss:** adding Redis without solving cache invalidation,
stampedes, tenant isolation, or what happens when Redis disappears.

## 6. Database engineering and data integrity

-   [ ] Choose the database according to access patterns and consistency
    requirements.
-   [ ] Design a normalized schema where appropriate.
-   [ ] Use indexes based on real query patterns.
-   [ ] Analyze slow queries with query plans.
-   [ ] Avoid N+1 query patterns.
-   [ ] Use parameterized queries or safe ORM operations.
-   [ ] Enforce foreign keys, unique constraints, and check constraints.
-   [ ] Use transactions for logically atomic operations.
-   [ ] Define isolation levels and handle serialization conflicts.
-   [ ] Prevent lost updates and duplicate business operations.
-   [ ] Configure connection pooling and pool limits.
-   [ ] Bound query execution times and statement concurrency.
-   [ ] Avoid unbounded result sets; use pagination.
-   [ ] Prefer keyset pagination for large, changing datasets where
    appropriate.
-   [ ] Implement database migrations with compatibility checks.
-   [ ] Separate read and write workloads when justified.
-   [ ] Evaluate read replicas and understand replication lag.
-   [ ] Partition or shard only when evidence supports it.
-   [ ] Define tenant isolation and data ownership constraints.
-   [ ] Design archival and retention policies.
-   [ ] Implement safe deletion and privacy-request workflows.
-   [ ] Monitor deadlocks, lock contention, replication lag,
    connections, IOPS and storage.
-   [ ] Test database failover and connection recovery.
-   [ ] Protect against accidental full-table scans and expensive
    queries.
-   [ ] Maintain verified backups and point-in-time recovery where
    supported.

**Critical distinction:** a replica improves some read workloads; it
does not automatically solve write bottlenecks, hot partitions, poor
indexing, or transaction contention.

## 7. Asynchronous processing and distributed systems

Long-running work should not unnecessarily occupy an API request.

-   [ ] Use message queues for background work where appropriate.
-   [ ] Separate producers, consumers, and task workers.
-   [ ] Define delivery semantics: at-most-once, at-least-once, or
    effectively-once business behavior.
-   [ ] Assume a task may execute more than once.
-   [ ] Make consumers idempotent.
-   [ ] Configure acknowledgements and visibility timeouts correctly.
-   [ ] Add retry policies and dead-letter queues.
-   [ ] Prevent poison messages from looping forever.
-   [ ] Implement queue depth and queue-age alerts.
-   [ ] Scale workers based on backlog and processing latency.
-   [ ] Set concurrency and resource limits per worker.
-   [ ] Preserve ordering only where the business requires it.
-   [ ] Handle duplicate and out-of-order events.
-   [ ] Use the transactional outbox pattern when database updates and
    event publication must stay consistent.
-   [ ] Use deduplication and reconciliation for critical workflows.
-   [ ] Propagate trace IDs and correlation IDs across asynchronous
    boundaries.
-   [ ] Set expiration and cancellation policies for abandoned tasks.
-   [ ] Prevent one tenant from exhausting shared worker capacity.
-   [ ] Design for broker downtime and consumer restarts.

Examples include email delivery, report generation, document processing,
payment workflows, notifications, data imports and AI inference jobs.

## 8. Network, edge, and API infrastructure

-   [ ] CDN for static delivery and suitable edge caching.
-   [ ] DDoS protection at the network and application layers.
-   [ ] Web application firewall (WAF).
-   [ ] API gateway or equivalent edge policy enforcement.
-   [ ] Load balancer health checks.
-   [ ] TLS everywhere and automated certificate renewal.
-   [ ] HSTS and secure transport configuration.
-   [ ] Restrict direct access to application origins.
-   [ ] Firewall rules and security groups with least privilege.
-   [ ] Private subnets for databases and internal services.
-   [ ] Network segmentation and egress controls.
-   [ ] DNS security and reliable failover configuration.
-   [ ] IPv4/IPv6 coverage where relevant.
-   [ ] Request body, header, upload, and URL-length limits.
-   [ ] Connection, idle, read, write and upstream timeouts.
-   [ ] WebSocket and long-lived connection limits.
-   [ ] Correct handling of trusted reverse proxies and client IP
    addresses.
-   [ ] Prevent abuse of open redirects and untrusted redirect
    destinations.
-   [ ] Prevent SSRF through outbound network restrictions.
-   [ ] Use private service connectivity where supported.
-   [ ] Test behavior when the CDN, gateway, DNS provider or upstream
    service fails.

## 9. Authentication, authorization, and identity

Authentication answers who a user is. Authorization answers what that
user may do. They are separate controls.

-   [ ] Use a well-maintained identity provider or mature authentication
    library.
-   [ ] Use OIDC/OAuth 2.0 correctly for applicable identity flows.
-   [ ] Use secure password hashing, such as Argon2id where appropriate.
-   [ ] Enforce MFA for privileged and sensitive accounts.
-   [ ] Implement secure account recovery and email verification.
-   [ ] Protect login, registration, password reset and OTP endpoints
    against abuse.
-   [ ] Prevent user enumeration.
-   [ ] Rotate sessions after authentication and privilege changes.
-   [ ] Configure secure, HttpOnly and appropriate SameSite cookies.
-   [ ] Apply CSRF protections when applicable.
-   [ ] Set reasonable access-token lifetimes.
-   [ ] Implement refresh-token rotation and revocation where
    appropriate.
-   [ ] Define session expiration and global logout behavior.
-   [ ] Revoke credentials after compromise or account recovery.
-   [ ] Apply authorization checks on every sensitive server-side
    operation.
-   [ ] Prevent IDOR/BOLA: changing an object ID must not grant access.
-   [ ] Enforce tenant isolation at the data-access layer.
-   [ ] Use RBAC, ABAC or a combination based on authorization
    complexity.
-   [ ] Apply least privilege to users, services and administrators.
-   [ ] Require stronger controls for privileged operations.
-   [ ] Audit role changes and administrative actions.
-   [ ] Protect service-to-service identities and credentials.
-   [ ] Define break-glass access with strict audit and expiry.
-   [ ] Test authorization across all roles, tenants and object types.

## 10. Application security: OWASP and common vulnerabilities

Use the current OWASP Top 10 and OWASP API Security Top 10 as baselines,
then extend testing to your application's actual threat model.

  -----------------------------------------------------------------------
  Threat                              Essential defenses
  ----------------------------------- -----------------------------------
  SQL/NoSQL injection                 Parameterized queries, safe
                                      operators, input validation,
                                      least-privilege database accounts

  Cross-site scripting (XSS)          Context-aware output encoding, safe
                                      rendering, sanitization when HTML
                                      is allowed, CSP

  CSRF                                Anti-CSRF tokens, SameSite cookies,
                                      origin validation where appropriate

  SSRF                                Destination allowlists, IP-range
                                      restrictions, egress filtering,
                                      DNS-rebinding defenses

  Broken access control               Server-side object, function and
                                      tenant authorization

  Broken authentication               Secure sessions, MFA, recovery
                                      protections, credential-stuffing
                                      defenses

  Security misconfiguration           Secure defaults, hardened
                                      deployments, configuration auditing

  Insecure deserialization            Safe data formats, strict schemas,
                                      no untrusted object deserialization

  Path traversal                      Canonicalized paths, allowlisted
                                      file access, isolated storage

  Command injection                   Avoid shell execution; use safe
                                      APIs and argument separation

  Request smuggling                   Consistent HTTP parsing, patched
                                      proxies and servers, tested edge
                                      configurations

  Prototype pollution                 Safe object handling, schema
                                      validation, patched dependencies

  Mass assignment                     Explicitly allowlisted writable
                                      fields

  Open redirects                      Validated redirect destinations

  File upload attacks                 Content validation, size limits,
                                      quarantine, isolated processing

  Race conditions                     Atomic updates, constraints,
                                      transactions, idempotency

  Resource exhaustion                 Request limits, bounded
                                      concurrency, quotas, timeouts and
                                      load shedding

  Information leakage                 Sanitized errors, controlled
                                      responses, secret redaction
  -----------------------------------------------------------------------

Additional controls:

-   [ ] Strict input validation using schemas.
-   [ ] Output encoding appropriate to the destination context.
-   [ ] Secure HTTP security headers.
-   [ ] Content Security Policy with a tested rollout.
-   [ ] CORS allowlists; never treat CORS as authentication.
-   [ ] Request smuggling and proxy-parser consistency tests where
    relevant.
-   [ ] File type, extension, content-signature and size checks.
-   [ ] Malware scanning or sandboxing for untrusted files where
    appropriate.
-   [ ] Archive extraction safeguards against path traversal and
    decompression bombs.
-   [ ] Safe image, PDF, and document processing.
-   [ ] No arbitrary file paths, template execution or unsafe expression
    evaluation.
-   [ ] Explicit allowlists for outbound integrations.
-   [ ] Security regression tests for fixed vulnerabilities.
-   [ ] No sensitive information in URLs, analytics, error messages or
    browser storage unnecessarily.

## 11. DDoS, bots, scraping, and API abuse

These are related but distinct problems. A WAF alone will not protect
every expensive endpoint, and a rate limit alone will not stop a large
distributed volumetric attack.

-   [ ] Upstream volumetric DDoS mitigation through an appropriate
    provider.
-   [ ] Network-layer and application-layer attack controls.
-   [ ] WAF rules for common exploit and abuse patterns.
-   [ ] Per-IP, per-account, per-API-key and per-tenant rate limits.
-   [ ] Distributed rate limiting where multiple application instances
    must share quotas.
-   [ ] Concurrency limits and request budgets.
-   [ ] Endpoint-specific quotas based on computational cost.
-   [ ] Separate limits for login, OTP, search, uploads, exports and AI
    inference.
-   [ ] Token-bucket or sliding-window rate limiting as appropriate.
-   [ ] Bot detection, behavioral analysis and challenge mechanisms
    where justified.
-   [ ] Credential-stuffing and password-spraying defenses.
-   [ ] Protection against enumeration and automated account creation.
-   [ ] Pagination and query-complexity limits.
-   [ ] GraphQL depth, breadth and cost limits if using GraphQL.
-   [ ] Search, report and export timeouts.
-   [ ] Restrictions on expensive filters, regexes and wildcard
    searches.
-   [ ] Request deduplication and idempotency for sensitive actions.
-   [ ] Quotas for bandwidth, storage, compute and external API usage.
-   [ ] Alerting on abnormal traffic patterns and sudden cost increases.
-   [ ] Origin protection so attackers cannot bypass edge controls.
-   [ ] Emergency throttling and feature-disable mechanisms.
-   [ ] A tested provider escalation and incident-response procedure.

For web scraping, add contractual and technical access controls, robots
directives where appropriate, abuse detection and sensible request
limits. **No technique can guarantee that publicly accessible
information will never be scraped.** Focus on protecting private data,
expensive operations, and infrastructure capacity.

## 12. Encryption, cryptography, and secrets management

Encryption is not a single checkbox. You need to protect data while it
travels, while it is stored, and while authorized services use it.

-   [ ] TLS for external and internal connections where appropriate.
-   [ ] Strong, modern cryptographic algorithms and protocol
    configurations.
-   [ ] Encryption at rest for databases, disks, object storage,
    snapshots and backups.
-   [ ] Application-level field encryption for particularly sensitive
    data when justified.
-   [ ] Envelope encryption using data-encryption keys and
    key-encryption keys.
-   [ ] Managed KMS or HSM for appropriate key-management requirements.
-   [ ] Separate key-management permissions from application-data
    permissions.
-   [ ] Automated key rotation policies and documented rotation
    procedures.
-   [ ] Key revocation and compromise-response procedures.
-   [ ] Separate keys across environments and tenants where the risk
    model requires it.
-   [ ] Avoid storing encryption keys beside the encrypted data they
    protect.
-   [ ] Use a secrets manager rather than hardcoded credentials or
    committed `.env` files.
-   [ ] Use short-lived credentials instead of long-lived cloud access
    keys where possible.
-   [ ] Prevent secrets from appearing in logs, traces, error reports,
    source maps or CI output.
-   [ ] Scan repositories and commit history for exposed secrets.
-   [ ] Remove or rotate exposed credentials immediately.
-   [ ] Use cryptographically secure random-number generators.
-   [ ] Use established libraries instead of custom cryptographic
    algorithms.
-   [ ] Verify webhook signatures and enforce replay protection.
-   [ ] Hash passwords with a password-hashing algorithm, not ordinary
    SHA-256.
-   [ ] Use HMAC or digital signatures when integrity/authenticity
    requirements call for them.
-   [ ] Separate hashing, encryption and signing; they solve different
    problems.
-   [ ] Define data classification and encryption requirements for each
    category.
-   [ ] Manage certificate issuance, renewal, revocation and expiry.
-   [ ] Ensure backups can actually be decrypted and restored with the
    required keys.

**Subtle failure:** excellent encryption is useless if an attacker
compromises the application's identity and can ask KMS to decrypt
everything. Key permissions, authorization, segmentation and
auditability matter just as much.

## 13. Cloud infrastructure and platform security

-   [ ] Infrastructure as Code (IaC) for repeatable infrastructure.
-   [ ] Version-controlled Terraform/OpenTofu or equivalent
    configuration.
-   [ ] Separate cloud accounts or projects for production and
    non-production where appropriate.
-   [ ] Private networking for databases and internal services.
-   [ ] Least-privilege IAM roles and policies.
-   [ ] Workload identity rather than static cloud credentials.
-   [ ] Restricted administrative access and MFA.
-   [ ] Cloud security posture monitoring.
-   [ ] Detection of publicly exposed storage, databases and management
    ports.
-   [ ] Configuration drift detection.
-   [ ] Resource quotas and service-limit monitoring.
-   [ ] Container images built from trusted, minimal base images.
-   [ ] Run containers as non-root where feasible.
-   [ ] Drop unnecessary Linux capabilities.
-   [ ] Use read-only filesystems where practical.
-   [ ] Apply CPU, memory, process and ephemeral-storage limits.
-   [ ] Scan images for vulnerabilities and embedded secrets.
-   [ ] Sign and verify release artifacts.
-   [ ] Generate and retain software bills of materials (SBOMs).
-   [ ] Pin trusted actions and dependencies in build pipelines.
-   [ ] Protect CI runners and isolate untrusted jobs.
-   [ ] Apply Kubernetes RBAC, network policies and workload security
    controls if using Kubernetes.
-   [ ] Restrict access to container runtimes and orchestration APIs.
-   [ ] Use admission policies to prevent insecure workloads.
-   [ ] Patch operating systems, runtimes, images and platform
    components.
-   [ ] Secure administrative consoles and management interfaces.
-   [ ] Enforce cloud egress restrictions where possible.
-   [ ] Monitor cloud audit logs and suspicious privilege escalation.
-   [ ] Set cloud spending alerts, resource budgets and emergency
    quotas.

Do not deploy Kubernetes simply because you want to support millions of
users. A managed application platform may be simpler and safer until the
operational requirements justify Kubernetes.

## 14. Software supply-chain security

A secure application can still be compromised by its dependencies, build
system or release process.

-   [ ] Software Composition Analysis (SCA).
-   [ ] Vulnerability scanning for direct and transitive dependencies.
-   [ ] Dependency lockfiles and controlled updates.
-   [ ] Package provenance and trusted registries.
-   [ ] Secret scanning.
-   [ ] Static Application Security Testing (SAST).
-   [ ] Dynamic Application Security Testing (DAST).
-   [ ] Infrastructure-as-Code scanning.
-   [ ] Container image scanning.
-   [ ] License compliance checks.
-   [ ] SBOM generation and retention.
-   [ ] Artifact signing and verification.
-   [ ] Build provenance and traceability.
-   [ ] Pinned CI actions and controlled build dependencies.
-   [ ] Protected release signing keys.
-   [ ] A process for emergency dependency patching.
-   [ ] A process for responding to a compromised upstream package.
-   [ ] Periodic review of abandoned or unmaintained dependencies.

Useful standards and frameworks include [NIST
SSDF](https://csrc.nist.gov/Projects/ssdf) and
[SLSA](https://slsa.dev/).

## 15. Observability, logging, and alerting

If you cannot identify why the system is failing, you cannot operate it
reliably.

### Metrics

Request rate, error rate, latency, saturation, CPU, memory, database
pools, cache performance and queue backlog.

### Structured logs

Request IDs, trace IDs, actor/service identity, operation, result,
duration and relevant error context.

### Distributed tracing

Follow a request through the gateway, API, cache, database, queue and
downstream services.

Checklist:

-   [ ] Centralized log collection and searchable retention.
-   [ ] Consistent correlation and trace IDs.
-   [ ] OpenTelemetry instrumentation where appropriate.
-   [ ] RED metrics: rate, errors, duration.
-   [ ] USE metrics: utilization, saturation, errors for infrastructure
    resources.
-   [ ] Business metrics alongside technical metrics.
-   [ ] Service-level indicators and objective tracking.
-   [ ] Alerts tied to user impact, SLO burn rate and critical
    infrastructure failures.
-   [ ] Separate actionable alerts from informational events.
-   [ ] Alert deduplication and noise reduction.
-   [ ] On-call routing and escalation.
-   [ ] Dashboards for dependencies and individual services.
-   [ ] Audit logs for privileged and security-sensitive events.
-   [ ] Redaction of tokens, passwords, personal data and sensitive
    payloads.
-   [ ] Sampling policies that preserve critical error and security
    events.
-   [ ] Log storage quotas, retention limits and access controls.
-   [ ] Monitoring for telemetry pipeline failure.
-   [ ] Synthetic checks and end-to-end health probes.
-   [ ] Detection of unusual latency, error rates, queue growth and
    spending.
-   [ ] Runbooks linked directly from alerts.

**Never use a successful `/health` response as proof that the
application is healthy.** A process can be alive while its database is
unavailable, its queue is blocked, or its critical workflow is broken.

## 16. Testing and performance validation

-   [ ] Unit tests for business logic.
-   [ ] Integration tests against realistic dependencies.
-   [ ] API contract tests.
-   [ ] End-to-end tests for critical user journeys.
-   [ ] Authorization and tenant-isolation tests.
-   [ ] Database migration and rollback-compatibility tests.
-   [ ] Regression tests for every significant production incident.
-   [ ] Property-based testing for complex input and business rules
    where useful.
-   [ ] Fuzz testing for parsers, file processors and exposed interfaces
    where appropriate.
-   [ ] Accessibility and cross-browser testing.
-   [ ] Dependency failure and timeout tests.
-   [ ] Load testing at expected traffic.
-   [ ] Stress testing beyond expected traffic.
-   [ ] Spike testing for sudden traffic surges.
-   [ ] Soak testing to find memory leaks and long-running degradation.
-   [ ] Capacity testing to determine maximum sustainable throughput.
-   [ ] Concurrent-write and race-condition tests.
-   [ ] Failover testing.
-   [ ] Recovery testing after process and infrastructure restarts.
-   [ ] Test realistic payload sizes and user behavior.
-   [ ] Measure p50, p95 and p99 latency, error rates and throughput.
-   [ ] Test with production-like database sizes and index
    distributions.
-   [ ] Verify autoscaling behavior and scale-up delays.
-   [ ] Measure cold starts and dependency connection storms.
-   [ ] Test overload behavior, queue limits and backpressure.
-   [ ] Profile CPU, memory, I/O and network bottlenecks.
-   [ ] Repeat tests after major architectural or infrastructure
    changes.

Use tools such as k6, Locust or Gatling according to your stack. Load
testing must be authorized and performed against controlled systems.

## 17. Backups, disaster recovery, and business continuity

-   [ ] Define Recovery Point Objective (RPO): acceptable data loss.
-   [ ] Define Recovery Time Objective (RTO): acceptable restoration
    time.
-   [ ] Automated database backups.
-   [ ] Point-in-time recovery where needed.
-   [ ] Backup encryption and access separation.
-   [ ] Immutable or isolated backups to resist destructive attacks.
-   [ ] Replication across appropriate failure domains.
-   [ ] Backup retention and deletion policies.
-   [ ] Object-storage versioning where appropriate.
-   [ ] Recovery procedures for databases, object storage, configuration
    and keys.
-   [ ] Restore tests at scheduled intervals.
-   [ ] Full disaster-recovery exercises.
-   [ ] Documented regional failover procedures where applicable.
-   [ ] Dependency and vendor outage plans.
-   [ ] Recovery of CI/CD, identity and infrastructure-management
    access.
-   [ ] Incident communications and stakeholder escalation.
-   [ ] Data-integrity verification after restoration.
-   [ ] Reconciliation of incomplete transactions and background jobs.
-   [ ] Defined ownership for recovery tasks.

A green backup job is not evidence of recoverability. A successfully
restored system is.

## 18. Privacy, compliance, and data governance

-   [ ] Data inventory and classification.
-   [ ] Collect only data necessary for the product.
-   [ ] Document processing purposes and retention periods.
-   [ ] Define lawful processing requirements for relevant
    jurisdictions.
-   [ ] User consent and preference management where required.
-   [ ] Data access, export, correction and deletion workflows where
    applicable.
-   [ ] Retention and deletion enforcement across databases, caches,
    logs and object storage.
-   [ ] Document backup-expiration implications for deletion requests.
-   [ ] Tenant isolation and cross-customer data protection.
-   [ ] Redaction or minimization of personal data in telemetry.
-   [ ] Restrict employee access to customer information.
-   [ ] Audit access to sensitive records.
-   [ ] Review third-party processors and data-sharing arrangements.
-   [ ] Document data residency requirements.
-   [ ] Assess applicable Indian DPDP Act obligations and other relevant
    privacy laws.
-   [ ] Maintain incident and breach-notification procedures.
-   [ ] Avoid using production personal data in development unless
    appropriately protected.
-   [ ] Define procedures for legal holds and retention exceptions.

Compliance depends on the product, its users, data types, jurisdictions
and actual processing. A privacy policy alone does not establish
compliance.

## 19. Frontend and client-side production quality

A strong backend cannot compensate for an insecure or unreliable client.

-   [ ] Responsive layouts and mobile compatibility.
-   [ ] Loading, empty, error, success and offline states.
-   [ ] Graceful handling of slow or failed API calls.
-   [ ] Request cancellation when users navigate away.
-   [ ] Debouncing and throttling of expensive UI interactions.
-   [ ] Pagination or virtualization for large datasets.
-   [ ] Optimized bundles, code splitting and lazy loading.
-   [ ] Image optimization and appropriate CDN caching.
-   [ ] Web Core Vitals and browser performance monitoring.
-   [ ] Accessible keyboard navigation and screen-reader support.
-   [ ] Semantic HTML and accessible form validation.
-   [ ] Secure dependency and browser API usage.
-   [ ] No secrets embedded in frontend code or source maps.
-   [ ] No reliance on frontend checks for authorization.
-   [ ] Safe rendering of user-generated content.
-   [ ] Session-expiry and authentication-refresh handling.
-   [ ] Protection against duplicate submissions.
-   [ ] Robust handling of network reconnects.
-   [ ] WebSocket reconnection with backoff and subscription recovery.
-   [ ] Cross-browser and device testing.
-   [ ] Browser storage and service-worker cache security.
-   [ ] Clear error messages without internal stack traces.
-   [ ] Content Security Policy and other applicable security headers.

## 20. API design and developer experience

-   [ ] OpenAPI specifications for REST APIs where applicable.
-   [ ] Request and response schema validation.
-   [ ] Consistent error codes and error formats.
-   [ ] API versioning and deprecation policy.
-   [ ] Pagination, filtering and sorting limits.
-   [ ] Maximum request-body and response sizes.
-   [ ] Timeouts and cancellation semantics.
-   [ ] Idempotency for retryable operations.
-   [ ] Quotas and API-key management where applicable.
-   [ ] Webhook signature validation and replay protection.
-   [ ] Webhook retries, deduplication and delivery logs.
-   [ ] SDK compatibility and API contract testing.
-   [ ] Backward-compatible evolution of fields.
-   [ ] Deprecation warnings and migration windows.
-   [ ] Documentation for authentication, errors, limits and examples.
-   [ ] Safe defaults for every exposed endpoint.
-   [ ] Administrative APIs isolated from public interfaces.
-   [ ] Separate public, internal and privileged API surfaces.
-   [ ] Request tracing across client and server.
-   [ ] Explicitly documented consistency and delivery guarantees.

## 21. Business logic and transactional correctness

These failures may not crash the server, but they can corrupt data or
cost money.

-   [ ] Define invariants for critical business entities.
-   [ ] Enforce invariants on the server and, where possible, in the
    database.
-   [ ] Make state transitions explicit and validated.
-   [ ] Prevent duplicate payments, submissions, reservations or jobs.
-   [ ] Handle simultaneous requests that modify the same record.
-   [ ] Define cancellation and refund semantics where applicable.
-   [ ] Use transactional boundaries around logically atomic operations.
-   [ ] Make multi-step workflows resumable.
-   [ ] Record audit trails for high-impact state changes.
-   [ ] Use reconciliation jobs to identify inconsistencies.
-   [ ] Avoid assuming that an external API call and a database
    transaction are atomic.
-   [ ] Handle partial failure between external services.
-   [ ] Define behavior when a request times out after the operation
    succeeds.
-   [ ] Test duplicate messages and repeated client submissions.
-   [ ] Validate quantities, currencies, units, timestamps and numeric
    boundaries.
-   [ ] Establish explicit rules for time zones, daylight-saving changes
    and date boundaries.
-   [ ] Define retention and lifecycle transitions.
-   [ ] Make administrative overrides auditable.

## 22. Cost, efficiency, and resource governance

An application can technically scale to millions of users and still
become financially unsustainable.

-   [ ] Define a budget for infrastructure and third-party services.
-   [ ] Track cost per active user, request, workflow and AI inference.
-   [ ] Monitor database storage, IOPS, network egress and backup costs.
-   [ ] Monitor CDN, logging and telemetry costs.
-   [ ] Set budget alerts and anomaly detection.
-   [ ] Apply resource quotas per tenant.
-   [ ] Control expensive external API usage.
-   [ ] Use caching where it reduces measured cost and latency.
-   [ ] Use right-sized compute and storage.
-   [ ] Scale down safely when demand drops.
-   [ ] Avoid uncontrolled autoscaling.
-   [ ] Bound retries and queue growth.
-   [ ] Enforce maximum upload, export and inference sizes.
-   [ ] Use lifecycle policies for old data and objects.
-   [ ] Sample or filter low-value telemetry without losing essential
    audit records.
-   [ ] Review idle resources and orphaned cloud assets.
-   [ ] Load-test the cost of peak demand, not just its technical
    throughput.
-   [ ] Establish emergency spend controls without breaking critical
    workflows.

## 23. AI/ML, NLP, and LLM-specific production safeguards

For AI-powered products, conventional application security is necessary
but insufficient.

### Model serving and inference

-   [ ] Separate model inference from latency-sensitive API handlers
    when appropriate.
-   [ ] Model versioning and reproducible artifacts.
-   [ ] Model registry and controlled promotion between environments.
-   [ ] Input size, token, sequence-length and inference-time limits.
-   [ ] Request batching where it improves throughput.
-   [ ] GPU/CPU memory monitoring and admission control.
-   [ ] Inference queues with bounded capacity.
-   [ ] Model warm-up and cold-start testing.
-   [ ] Concurrency limits per model and tenant.
-   [ ] Model fallback or graceful degradation.
-   [ ] Timeouts, cancellation and protection against runaway
    generation.
-   [ ] Model-specific performance and quality regression tests.
-   [ ] Model artifact integrity and access controls.
-   [ ] Reproducible training and inference configurations.
-   [ ] Track inference cost, latency, throughput and failure rates.

### Data and model integrity

-   [ ] Validate training and evaluation data provenance.
-   [ ] Detect duplicate, corrupted and malformed samples.
-   [ ] Protect against training-data poisoning.
-   [ ] Prevent train/test leakage.
-   [ ] Track dataset and feature versions.
-   [ ] Restrict access to training data and model artifacts.
-   [ ] Monitor data drift and concept drift.
-   [ ] Detect model quality degradation.
-   [ ] Establish model rollback procedures.
-   [ ] Test against adversarial inputs relevant to the application.
-   [ ] Monitor for model extraction and excessive probing.
-   [ ] Evaluate privacy risks from training data and model outputs.

### LLM, RAG, and AI-agent security

-   [ ] Treat retrieved documents and user-provided content as
    untrusted.
-   [ ] Defend against direct and indirect prompt injection.
-   [ ] Enforce authorization on retrieval, not just on the final
    answer.
-   [ ] Apply tenant and document-level access controls to vector
    search.
-   [ ] Prevent cross-tenant retrieval and embedding-cache leakage.
-   [ ] Validate and constrain tool calls.
-   [ ] Apply least privilege to agent tools.
-   [ ] Require approval for consequential or irreversible actions.
-   [ ] Enforce tool-call timeouts, budgets and iteration limits.
-   [ ] Prevent SSRF and unsafe URL fetching by retrieval tools.
-   [ ] Sanitize or isolate untrusted document parsing.
-   [ ] Defend against malicious PDFs, HTML, archives and embedded
    instructions.
-   [ ] Validate structured model output against strict schemas.
-   [ ] Never treat generated SQL or shell commands as trusted code.
-   [ ] Apply authorization and query restrictions to model-generated
    operations.
-   [ ] Prevent sensitive system prompts, credentials and internal data
    from leaking.
-   [ ] Establish PII redaction and sensitive-output controls where
    appropriate.
-   [ ] Evaluate hallucination rates and groundedness for critical
    tasks.
-   [ ] Measure retrieval precision and recall.
-   [ ] Test citation correctness and source attribution.
-   [ ] Run prompt-injection and data-exfiltration evaluations.
-   [ ] Log model versions and tool invocations without unnecessarily
    retaining sensitive prompts.
-   [ ] Maintain human-review pathways for high-impact decisions.
-   [ ] Track quality, safety and cost after every model or prompt
    change.

For AI products, prompt engineering is not a security boundary.
**Enforce permissions in deterministic application code and at the
data-access layer.**

## 24. Incident response and security operations

-   [ ] Written incident-response plan.
-   [ ] Defined severity levels and escalation criteria.
-   [ ] Named incident commander and technical responders.
-   [ ] Procedures for credential theft, data leakage, DDoS and
    ransomware.
-   [ ] Automated alerts for suspicious authentication and privilege
    escalation.
-   [ ] Centralized, tamper-resistant security audit logs.
-   [ ] Playbooks for compromised accounts, keys and cloud identities.
-   [ ] Emergency credential and certificate rotation.
-   [ ] Isolation and containment procedures.
-   [ ] Forensic evidence preservation.
-   [ ] Tested communication and notification procedures.
-   [ ] Incident timeline and decision logging.
-   [ ] Post-incident root-cause analysis without blame.
-   [ ] Tracked corrective actions with owners and deadlines.
-   [ ] Regular incident simulations and tabletop exercises.
-   [ ] Vulnerability disclosure and security-reporting process.
-   [ ] Defined patching SLAs based on vulnerability severity and
    exposure.
-   [ ] Continuous review of security alerts and threat intelligence.
-   [ ] Periodic penetration tests by qualified testers.
-   [ ] Periodic review of third-party security posture.

## 25. Product readiness and operational maturity

These are the details that separate software that works from software
that can be operated as a dependable service.

-   [ ] Onboarding and account recovery workflows.
-   [ ] Clear user-facing error and status messages.
-   [ ] Service status page and incident communications.
-   [ ] Feature flags and controlled feature rollout.
-   [ ] User feedback and support workflows.
-   [ ] Admin dashboard with strict access controls.
-   [ ] Audit trail for administrative actions.
-   [ ] Data export and deletion functionality where required.
-   [ ] Accessibility testing.
-   [ ] Localization and timezone handling where relevant.
-   [ ] Usage quotas and account limits.
-   [ ] Abuse reporting and enforcement procedures.
-   [ ] Documentation for operators and support staff.
-   [ ] Operational runbooks for common failures.
-   [ ] Maintenance windows and deprecation policies where applicable.
-   [ ] Release notes and user-impact communication.
-   [ ] Dependency and vendor exit strategies.
-   [ ] Defined ownership for every critical component.
-   [ ] Regular review of technical debt and operational risks.
-   [ ] Production readiness review before major launches.

## 26. Standards and reference checklists to anchor your implementation

Do not invent your own security standard from scratch. Use established
frameworks as baselines.

### OWASP ASVS

Structured, testable application security requirements.

https://owasp.org/www-project-application-security-verification-standard/

### OWASP Top 10 and API Security Top 10

Common web and API security risk categories.

-   Web application risks: https://owasp.org/www-project-top-ten/
-   API security risks: https://owasp.org/www-project-api-security/

### NIST Cybersecurity Framework 2.0

Govern, Identify, Protect, Detect, Respond and Recover.

https://www.nist.gov/cyberframework

### NIST Secure Software Development Framework

Integrates secure development into the complete software lifecycle.

https://csrc.nist.gov/Projects/ssdf

### SLSA

Build integrity, provenance and software supply-chain protections.

https://slsa.dev/

### OpenTelemetry

Vendor-neutral instrumentation for traces, metrics and logs.

https://opentelemetry.io/docs/

## 27. The implementation priority: what to build first

Not everything belongs in version one. Build the essential controls
early, then scale complexity according to actual requirements.

### Phase 1 --- Secure foundation

-   [ ] Architecture and threat model.
-   [ ] Authentication and authorization.
-   [ ] Database constraints and safe queries.
-   [ ] Secrets and encryption.
-   [ ] Input validation and secure configuration.
-   [ ] Unit and integration tests.
-   [ ] CI pipeline and protected releases.

### Phase 2 --- Production readiness

-   [ ] CDN/WAF and edge rate limits.
-   [ ] Caching and cache invalidation.
-   [ ] Connection pooling and query optimization.
-   [ ] Centralized logs, metrics and traces.
-   [ ] Backups and tested restoration.
-   [ ] Dependency and container scanning.
-   [ ] Staging and rollback strategy.

### Phase 3 --- Scale and resilience

-   [ ] Load balancers and horizontal scaling.
-   [ ] Queues and isolated workers.
-   [ ] Backpressure, timeouts and circuit breakers.
-   [ ] Load, stress, spike and soak testing.
-   [ ] Capacity and cost modeling.
-   [ ] Failover and disaster-recovery exercises.
-   [ ] Autoscaling and overload controls.

### Phase 4 --- Advanced operations

-   [ ] Multi-region architecture if justified.
-   [ ] Advanced bot and abuse detection.
-   [ ] Supply-chain provenance and artifact signing.
-   [ ] Continuous security validation.
-   [ ] Advanced tenant isolation.
-   [ ] Automated incident-response playbooks.
-   [ ] AI-specific threat evaluations if applicable.

## 28. What I would use for a serious application

For a backend-focused, AI-enabled product, start with a **modular
monolith plus isolated background workers**, rather than immediately
deploying dozens of microservices.

  -----------------------------------------------------------------------
  Layer                               Starting choice
  ----------------------------------- -----------------------------------
  Frontend                            Next.js with TypeScript

  API/backend                         FastAPI or a comparable typed
                                      backend

  Primary database                    Managed PostgreSQL

  Distributed cache                   Managed Redis-compatible service

  Background jobs                     Durable queue with independent
                                      workers

  File storage                        Private object storage with
                                      encryption

  Edge security                       CDN, managed DDoS protection, WAF

  Identity                            Established OIDC identity provider

  Secrets and keys                    Cloud secrets manager and managed
                                      KMS

  Deployment                          Docker and a managed container
                                      platform

  CI/CD                               GitHub Actions or equivalent

  Infrastructure                      Terraform/OpenTofu

  Observability                       OpenTelemetry with managed or
                                      self-hosted backends

  Testing                             Pytest, API integration tests and
                                      k6/Locust

  AI components                       Separate inference workers, model
                                      versioning and explicit resource
                                      budgets
  -----------------------------------------------------------------------

The exact services should depend on the cloud provider, traffic profile,
compliance needs and operating budget.

### Five rules that matter most

1.  **Never trust the client.** Authorization, validation and business
    rules must be enforced server-side.
2.  **Never assume a dependency will always be available.** Use
    timeouts, bounded retries, backpressure and graceful degradation.
3.  **Never assume a successful deployment means a safe deployment.**
    Verify health, observe the rollout and maintain a tested rollback
    path.
4.  **Never claim scale without measurements.** Establish a workload
    model, test realistic traffic and identify the actual bottleneck.
5.  **Never treat security as a final development phase.** Build it into
    architecture, code review, CI/CD, infrastructure and operations.

The real objective is not to implement every technology on this list. It
is to ensure that every relevant failure mode has an appropriate
control, that the control works under realistic conditions, and that
somebody can detect and respond when it fails.
