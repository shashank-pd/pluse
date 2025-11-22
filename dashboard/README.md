# Pulse Dashboard (Pulse Autoscaler)

**Location:** `dashboard/` (Flask app at `dashboard/dashboard.py`, UI templates in `dashboard/templates/`)

**Purpose:** Provide a web UI for monitoring cluster state, viewing autoscaler logs and decisions, and triggering synthetic/custom metric events via Pub/Sub.

---

**HTTP API (used by the UI)**

- `GET /` — Dashboard HTML
- `GET /api/status` — Current cluster snapshot (replicas, nodes, last decision)
- `GET /api/metrics` — Recent metrics history
- `GET /api/decisions` — Recent scaling decisions
- `GET /api/logs?lines=<n>` — Autoscaler logs (returns `logs` array or string)
- `GET /api/nodes` — Node details
- `GET /api/pods` — Pod details
- `POST /api/test/<type>` — Run a synthetic test (e.g., `scale_up`, `scale_down`, `critical`, `high_latency`)
- `POST /api/trigger_load` — Publish a custom metric (JSON body)

Examples:

```bash
curl "http://localhost:5000/api/logs?lines=50"
curl -X POST "http://localhost:5000/api/trigger_load" -H 'Content-Type: application/json' -d '{"cpu":90,"latency":600,"error_rate":5,"severity":"CRITICAL"}'
```

---

**Deployment notes (Kubernetes / GKE)**

- The repo contains `k8s/` manifests for example deployments. Adjust image names and config as needed.
- Typical flow:
```bash
# build & push image (example)
docker build -t gcr.io/$PROJECT_ID/pulse-dashboard:latest dashboard/
docker push gcr.io/$PROJECT_ID/pulse-dashboard:latest

# apply manifests
kubectl apply -f k8s/dashboard-deploy.yaml
kubectl get svc pulse-dashboard
```

Wait for `EXTERNAL-IP` to be assigned and open `http://<EXTERNAL-IP>:5000`.

Notes:
- Ensure the dashboard Service has permissions to read pods/nodes (ServiceAccount + RBAC). See `k8s/rbac-autoscaler.yaml` for examples.


---


**Architecture**

- The dashboard reads K8s API for status, publishes custom metrics to Pub/Sub, and renders a single-page UI (Chart.js).
- The autoscaler subscribes to Pub/Sub and performs scaling decisions.

---

