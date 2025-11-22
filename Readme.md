<div align="center">
<h1>Pulse</h1>
<p><em><strong>Pulse is a production-focused autoscaling engine for Kubernetes</strong>, designed to scale <strong>Pods + Nodes</strong> based on real workload pressure, not just CPU.
It blends: Real application metrics (CPU, latency, error-rate), Pub/Sub queue pressure, Node health & taints, OOMKilled + memory pressure & Spike detection using sliding-window analytics  
</em></p>

<img src="https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/Flask-Dashboard-lightgrey?logo=flask&logoColor=white" />
<img src="https://img.shields.io/badge/Docker-Containerized-2496ED?logo=docker&logoColor=white" />
<img src="https://img.shields.io/badge/Kubernetes-1.26%2B-326CE5?logo=kubernetes&logoColor=white" />
<img src="https://img.shields.io/badge/GCP-Cloud-4285F4?logo=googlecloud&logoColor=white" />
<img src="https://img.shields.io/badge/PubSub-Event%20Driven-F9AB00?logo=googlecloud&logoColor=white" />
<img src="https://img.shields.io/badge/Monitoring-Stackdriver-34A853?logo=googlecloud&logoColor=white" />

<img src="https://img.shields.io/badge/Pods%20Autoscaling-Custom%20Logic-brightgreen?logo=kubernetes&logoColor=white" />
<img src="https://img.shields.io/badge/Node%20Autoscaling-Custom%20Scaler-orange?logo=googlecloud&logoColor=white" />
<img src="https://img.shields.io/badge/Backlog%20Scaling-PubSub%20Aware-blueviolet" />

<img src="https://img.shields.io/badge/Latency%20SLOs-p95%20%2F%20p99-critical?color=purple" />
<img src="https://img.shields.io/badge/Composite%20Scoring-Multi%20Metric-success?color=brightgreen" />
<img src="https://img.shields.io/badge/Spike%20Detection-30s%20vs%205min-ff69b4" />
<img src="https://img.shields.io/badge/Cooldowns-Thrashing%20Protection-blue" />

<img src="https://img.shields.io/badge/Node%20Health-Quarantine%20%26%20Recovery-yellow" />
<img src="https://img.shields.io/badge/OOMKilled-Auto%20Memory%20Tuning-red" />
<img src="https://img.shields.io/badge/CrashLoopBackOff-Detection%20%26%20Safeguards-important" />

</div>

## Contents
- [Overview](#overview)
- [Project structure](#project-structure)
- [Feature Summary](#feature-summary)
- [Sliding Window & Metrics Processing](#sliding-window-and-metrics-processing)
- [Critical Interactions](#critical-interactions)
- [Some Concepts](#some-concepts)
- [RBAC & Security](#rbac--security)
- [Cost optimization & comparisons](#cost-optimization)
- [HPA vs Pulse Autoscaler](#hpa-vs-pulse-autoscaler)
- [KEDA vs Pulse Autoscaler](#keda-vs-pulse-autoscaler)
- [Run / Deploy / Test](#run-deploy-test)

<a id="overview"></a>
## Overview
This project contains a dashboard (Flask) and an autoscaler (subscriber) that together demonstrate an advanced autoscaling strategy for Kubernetes workloads. The system consumes application metrics (via Pub/Sub), computes rolling statistics, makes scaling decisions (pod + node), and applies safe actions to the cluster.

Primary goals:
- Make scaling decisions based on combined signals (composite score) rather than a single metric.
- Detect spikes and critical events and react quickly while preserving safety via cooldowns and pod/node health checks.
- Optimize costs by scaling nodes based on real usage patterns and avoiding unnecessary node provisioning.

<a id="project-structure"></a>
## Project structure
Example important files:
- `dashboard/` — Flask UI and endpoints (`dashboard/dashboard.py`, templates in `dashboard/templates/`)
- `subscribers/autoscaler/` — autoscaler logic (`autoscaler.py`, `metrics_window.py`, `node_monitor.py`, `node_scaler.py`, `memory_optimizer.py`)
- `k8s/` — sample Kubernetes manifests and RBAC YAMLs
- `utils/ist.py` — IST timezone helpers

<a id="feature-summary"></a>
## Feature Summary

Below are the implemented features and a brief explanation of each:

- **Composite scoring system**: A weighted score (CPU, latency, error-rate) computed from a sliding window of recent metrics. The weights are configurable. Decisions to scale up/down are based on where this composite score sits relative to configured thresholds.
	
- **Latency-based scaling**: Uses p95/p99 latency targets to trigger scale-up when latency violates targets, separate from CPU-based policies.
	
- **Spike detection**: Detect instant traffic spikes by comparing last 30 seconds vs last 5 minutes.

- **Critical Event Bypass**: Critical events (severity=CRITICAL) bypass normal cooldowns and trigger emergency scale-up. Meant for severe outages or backlog explosions.
	
- **Pub/Sub backlog monitoring**: Monitors message backlog (size, oldest message age, growth rate) from Pub/Sub and triggers scaling when backlog is growing or messages age using Google Cloud Monitoring API.

- **Node health monitor**: Background thread continuously detects not-ready/cordoned nodes and calculates capacity loss. Can trigger emergency node scaling and quarantining logic.

- **Memory optimizer**: Detects OOMKilled containers and adjusts memory requests/limits for the workload using a safe increment and verification loop.

- **Usage-based node scaling**: Scales node pool (via Metric Server API) according to actual usage, not requested resources (saves costs)

- **Graceful node draining**: When scaling down or when quarantining nodes, it performs drain operations to respect PodDisruptionBudgets and gracefully evict pods (safely evicts pods). It's a 3-step process: 1. Cordon: mark node unschedulable; 2. Drain: evict pods with a 30s grace period; 3. Delete: optionally remove or deprovision the node from the pool.

- **Emergency unschedulable pod handling**: For critical failures (e.g., all nodes not-ready), system attempts emergency measures (scale up nodes, mark pods schedulable) to restore capacity.
	
- **Cooldown management**: Implements general and critical cooldowns to avoid thrash and cooldown windows 

<a id="sliding-window-and-metrics-processing"></a>
## Sliding Window & Metrics Processing
- The autoscaler uses a sliding time window (e.g., last N metrics points or last M seconds) collected into a fixed-size `deque` (see `metrics_window.py`). For each insert:
	- Metrics are stored along with timestamps.
	- Percentiles (p90/p95/p99), averages, medians and trends are computed.
	- Spike detection examines the recent delta compared to baseline to detect short bursts.

- Benefits:
	- Smooths noisy samples and reduces reactive instability.
	- Allows percentile-based reasoning for SLOs (p95/p99) rather than relying on sample averages.

Python Concepts:

	- `collections.deque` for efficient sliding-window buffers.

	- Numeric summary computations (percentile, median) and simple time-series trend detection.

	- Threading for background monitors and async Pub/Sub callbacks.
    
<a id="critical-interactions"></a>
## Critical Interactions (How it works)
- **OOMKilled detection**
	- The autoscaler inspects pod `last_state.terminated` and container statuses. When `reason == "OOMKilled"` is observed:
		- Record event in a memory-optimized store.
		- Trigger `MemoryOptimizer` which computes new memory limits (safe upward adjustments), patches the `Deployment` using Kubernetes API, and validates the change.


- **Node failure  [Emergency scaling]**
	- NodeMonitor periodically reads `Node` status conditions and taints. If nodes become `NotReady` or unschedulable beyond a threshold, the system:
		- Computes capacity lost (not-ready nodes / total nodes).
		- If loss exceeds configured threshold, calls NodeScaler to increase node-pool size.
		- Optionally cordons/quarantines nodes to avoid scheduling to bad nodes.


<a id="some-concepts"></a>
## Some Concepts:

- `google-cloud-pubsub` for event ingestion and synthetic metric publishing
- `google-cloud-monitoring` for backlog metrics
- `kubernetes` Python client for interacting with the cluster
- `flask` for the dashboard UI and simple REST endpoints
- `collections.deque`, custom windowing logic, percentile math
- Structured modules: `metrics_window.py`, `node_monitor.py`, `node_scaler.py`, `memory_optimizer.py`
- Background threads for periodic checks and Pub/Sub subscription callbacks
- Proper timezone handling for IST timestamps

## RBAC & Security

Pulse follows a **strict least-privilege RBAC model**, ensuring the autoscaler has *only the permissions genuinely required* to operate safely inside the cluster.

### Fine-grained, purpose built permissions

The autoscaler is granted only the minimal access needed:

* **Read-only:** Pods, Nodes, Deployments
* **Patch-only:** Deployment replicas (scaling), Node spec (cordon/drain)
* **Metrics access:** nodes + pods via `metrics.k8s.io`
* **Controlled pod eviction:** delete permissions limited *only* for safe draining operations, no broad delete powers.

### Separation of duties (Two ServiceAccounts)

To avoid privilege overload:

* **`pulse-ksa`** → Pod autoscaling (deployments, backlog decisions)
* **`pulse-node-scaler`** → Node autoscaling (cordon, drain, metrics evaluation)

Each component receives *just enough* access for its responsibility.

### No wildcard permissions

No `*` verbs, no `*` resources.
Everything is explicitly scoped to avoid accidental privilege escalation and to maintain predictable runtime behavior.

### Outcome

This RBAC configuration ensures:

* Secure autoscaling
* Safe node draining
* Compliance with Kubernetes security best-practices

Pulse scales aggressively when needed, but operates securely at all times.


<a id="cost-optimization"></a>
## Cost Optimization [Before vs After]
Naive scaling (request-based autoscaling or simple HPA on CPU) often leads to over-provisioning or reactive scaling that costs money.

- **Before (request-based / naive HPA)**
	- Typical HPA scales based on request count or per-pod CPU. This can cause noisy scale changes, inability to react to backlog in message systems, and inefficient node usage.
- **After (usage-based composite autoscaler in this project)**
	- Decisions are derived from a composite score (CPU + latency + error-rate) and Pub/Sub backlog indicators. This reduces false positives (noisy spikes) and focuses scaling on real workload pressure.


## HPA vs Pulse Autoscaler

**Horizontal Pod Autoscaler (HPA)** scales pods based on **one or two simple metrics**, typically CPU or memory. It cannot combine multiple signals, detect runtime issues, or understand service health.

**Pulse Autoscaler** extends far beyond HPA:

* Uses **multiple metrics together**: CPU, latency (p95/p99), error rate, Pub/Sub backlog
* Detects **CrashLoopBackOff** and **OOMKilled** events
* Performs **trend and spike detection**
* Applies **composite scoring** instead of threshold-only scaling
* Includes **node-level autoscaling** and **memory optimization**, which HPA cannot do
* Designed for **production SLO-driven scaling**, not just resource percentage scaling

In short, **HPA = simple resource-based scaling**,
**Pulse = full-stack intelligent autoscaling system** combining pods, nodes, and health signals.


## KEDA vs Pulse Autoscaler

Pulse does **not** use KEDA. KEDA is an event-driven autoscaler that scales workloads based on single external triggers (like Pub/Sub lag, queue length, rate, queries) and relies on HPA underneath.

**Pulse takes a different approach:**

* Uses **multiple signals together**: CPU, latency (p95/p99), error rate, Pub/Sub backlog, CrashLoopBackOff, OOMKilled, and trend analysis
* Performs **composite scoring** instead of single-metric triggers
* Includes **node-level scaling** (cordon, drain, resize node pools)
* Includes **memory optimization logic** (adjusts limits on OOMKilled)
* Built for **production SLO-driven scaling** and high-load environments

In short, **KEDA is a single-trigger event autoscaler**, while **Pulse is a full custom autoscaling system** combining pod autoscaling, node autoscaling, and runtime health intelligence.


<a id="run-deploy-test"></a>
## Run & Deploy (quick)
Local dev (dashboard):
```bash
pip install -r dashboard/requirements.txt
python dashboard/dashboard.py
```

Autoscaler (local dev):
```bash
pip install -r subscribers/autoscaler/requirements.txt
python subscribers/autoscaler/autoscaler.py
```

Kubernetes deploy (example):
```bash
kubectl apply -f k8s/rbac-autoscaler.yaml
kubectl apply -f k8s/autoscaler-deploy.yaml
kubectl apply -f k8s/dashboard-deploy.yaml
```

Ensure Pub/Sub credentials and permissions are wired into pods (Workload Identity).

---
---
