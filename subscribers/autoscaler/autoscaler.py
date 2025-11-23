import os, sys, json, time, datetime, threading
from typing import Any, Dict, Optional, Tuple

from utils.ist import now_ist_dt, now_ist_iso
from google.cloud import pubsub_v1
from kubernetes import client, config as k8s_config

from metrics_window import MetricsWindow
from pubsub_monitor import PubSubMonitor
from node_monitor import NodeMonitor
from memory_optimizer import MemoryOptimizer
from node_scaler import NodeScaler
from config import (
    COMPOSITE_SCALE_UP, COMPOSITE_SCALE_DOWN,
    WEIGHT_CPU, WEIGHT_LATENCY, WEIGHT_ERRORS,
    LATENCY_P95_THRESHOLD, LATENCY_P99_THRESHOLD,
    MIN_REPLICAS, MAX_REPLICAS, COOLDOWN_SECONDS,
    MAX_CRASHLOOP_COUNT, OOM_SCALE_MULTIPLIER,
    BACKLOG_SIZE_HIGH, OLDEST_MESSAGE_AGE_HIGH,
    NODE_FAILURE_SCALE_MULTIPLIER, NODE_CAPACITY_LOSS_THRESHOLD,
    DEPLOYMENT_NAME, NAMESPACE
)

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), "w", buffering=1)


def init_k8s_clients():
    try:
        k8s_config.load_incluster_config()
        print("Loaded in-cluster config")
    except:
        k8s_config.load_kube_config()
        print("Loaded local kubeconfig")
    return client.AppsV1Api(), client.CoreV1Api()

apps_v1, core_v1 = init_k8s_clients()

metrics_window = MetricsWindow(window_size=5)
last_scale_time = None
crash_loop_pods, oom_events = {}, {}

PROJECT = os.environ.get("GCP_PROJECT", "pulse-477304")
SUBSCRIPTION = os.environ.get("EVENT_SUB", "events-sub")
subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(PROJECT, SUBSCRIPTION)

try:
    pubsub_monitor = PubSubMonitor(PROJECT, SUBSCRIPTION)
    node_monitor = NodeMonitor()
    memory_optimizer = MemoryOptimizer()
    print("Monitors initialized")
except Exception as e:
    print("Monitor init failed:", e)
    pubsub_monitor = node_monitor = memory_optimizer = None

try:
    node_scaler = NodeScaler(
        project_id=PROJECT,
        zone=os.environ.get("ZONE", "asia-south1-a"),
        cluster_name=os.environ.get("CLUSTER_NAME", "pulse-gke"),
        node_pool_name=os.environ.get("NODE_POOL_NAME", "cheap-pool"),
    )
    print("Node scaler initialized")
except Exception as e:
    print("Node scaler init failed:", e)
    node_scaler = None


def background_node_health_check():
    if not node_monitor:
        return
    print("Node health monitor running")
    while True:
        try:
            nh = node_monitor.check_node_health()
            healthy, total = nh["healthy_nodes"], nh["total_nodes"]

            if nh["not_ready_nodes"]:
                loss = node_monitor.get_node_capacity_loss()
                print(f"Node down={len(nh['not_ready_nodes'])}, capacity loss={loss*100:.1f}%")

                if healthy == 0:
                    print(f"All nodes down ({total}) - cannot scale")
                    time.sleep(30)
                    continue

                if loss > NODE_CAPACITY_LOSS_THRESHOLD:
                    try:
                        execute_scale(
                            "up",
                            bypass_cooldown=True,
                            multiplier=NODE_FAILURE_SCALE_MULTIPLIER,
                            reason=f"Node failure ({len(nh['not_ready_nodes'])} not-ready)"
                        )
                    except Exception as e:
                        print("Emergency scale failed:", e)
            time.sleep(30)

        except Exception as e:
            print("Node health check error:", e)
            time.sleep(60)


def background_node_autoscaling():
    if not node_scaler:
        return
    print("Node autoscaler running")
    while True:
        try:
            node_scaler.check_and_scale()
            time.sleep(120)
        except Exception as e:
            print("Node scaler error:", e)
            time.sleep(120)


def start_background_threads():
    if node_monitor:
        threading.Thread(target=background_node_health_check, daemon=True).start()
        print("Node monitor thread started")
    if node_scaler:
        threading.Thread(target=background_node_autoscaling, daemon=True).start()
        print("Node autoscaler thread started")


def calculate_composite_score(stats):
    cpu = min(stats.get("avg_cpu", 0), 100)
    lat_ratio = stats.get("latency_p95", 0) / LATENCY_P95_THRESHOLD if LATENCY_P95_THRESHOLD else 0
    latency = min(lat_ratio * 100, 100)
    err_ratio = stats.get("avg_error_rate", 0) / 10
    errors = min(err_ratio * 100, 100)

    comp = cpu * WEIGHT_CPU + latency * WEIGHT_LATENCY + errors * WEIGHT_ERRORS
    return {"composite": round(comp, 2), "breakdown": {"cpu": cpu, "latency": latency, "errors": errors}}


def check_pod_health():
    try:
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
        for pod in pods.items:
            name = pod.metadata.name
            statuses = pod.status.container_statuses or []
            for c in statuses:

                if c.state.waiting and c.state.waiting.reason == "CrashLoopBackOff":
                    crash_loop_pods[name] = crash_loop_pods.get(name, 0) + 1
                    if crash_loop_pods[name] >= MAX_CRASHLOOP_COUNT:
                        print(f"CRITICAL: {name} CrashLoopBackOff")
                        return "unhealthy"

                if c.last_state and c.last_state.terminated and c.last_state.terminated.reason == "OOMKilled":
                    oom_events[name] = now_ist_iso()
                    print(f"{name} OOMKilled")

                    if memory_optimizer:
                        if memory_optimizer.record_oom_event(name) and memory_optimizer.should_adjust_memory(DEPLOYMENT_NAME, NAMESPACE):
                            ok, old_mem, new_mem = memory_optimizer.adjust_memory_limits(DEPLOYMENT_NAME, NAMESPACE)
                            if ok:
                                print(f"Memory increased {old_mem} → {new_mem}")
                            return "oom"
                    return "oom"

                if c.state.running:
                    crash_loop_pods.pop(name, None)
        return "healthy"
    except Exception as e:
        print("Pod health error:", e)
        return "unknown"


def should_scale(stats, critical=False):
    if stats.get("count", 0) < 3:
        return None, "Not enough data"

    health = check_pod_health()
    if health == "unhealthy":
        return None, "Pods unhealthy"
    if health == "oom":
        return "up", "OOM detected"

    if node_monitor:
        try:
            nh = node_monitor.check_node_health()
            healthy = nh["healthy_nodes"]
            if nh["not_ready_nodes"]:
                loss = node_monitor.get_node_capacity_loss()
                print(f"Node failures: {len(nh['not_ready_nodes'])}, loss={loss*100:.1f}%")

                if healthy == 0:
                    return None, "No healthy nodes"

                if loss > NODE_CAPACITY_LOSS_THRESHOLD:
                    return "up", "Node failure"
        except Exception as e:
            print("Node health (decision) error:", e)

    if pubsub_monitor:
        try:
            b = pubsub_monitor.get_backlog_stats()
            if b["backlog_size"] > 0:
                print(f"Backlog={b['backlog_size']} oldest={b['oldest_message_age']}s")

            if b["backlog_size"] > BACKLOG_SIZE_HIGH:
                return "up", "Backlog high"
            if b["oldest_message_age"] > OLDEST_MESSAGE_AGE_HIGH:
                return "up", "Message age high"
        except Exception as e:
            print("PubSub check error:", e)

    if critical:
        return "up", "Critical event"

    comp = calculate_composite_score(stats)
    composite = comp["composite"]
    print(f"Composite={composite} breakdown={comp['breakdown']}")
    print(f"Latency p90={stats['latency_p90']} p95={stats['latency_p95']} p99={stats['latency_p99']}")

    violate = stats["latency_p95"] > LATENCY_P95_THRESHOLD or stats["latency_p99"] > LATENCY_P99_THRESHOLD

    if composite > COMPOSITE_SCALE_UP or violate or stats["spike_detected"]:
        return "up", "High load"

    if composite < COMPOSITE_SCALE_DOWN and stats["trend"] not in ["increasing", "spiking"]:
        return "down", "Low load"

    return None, "Normal"



def execute_scale(action, bypass_cooldown=False, multiplier=1.0, reason=""):
    global last_scale_time

    if last_scale_time and not bypass_cooldown:
        elapsed = (now_ist_dt() - last_scale_time).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            print(f"Cooldown {int(COOLDOWN_SECONDS - elapsed)}s left")
            return False

    try:
        dep = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        current = dep.spec.replicas or MIN_REPLICAS

        if action == "up":
            inc = max(1, int(current * (multiplier - 1))) if multiplier > 1 else 1
            new = min(current + inc, MAX_REPLICAS)
        else:
            new = max(current - 1, MIN_REPLICAS)

        if new == current:
            print("Scale limit reached")
            return False

        dep.spec.replicas = new
        apps_v1.patch_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE, dep)

        last_scale_time = now_ist_dt()
        print(f"Scaled {action}: {current} → {new}")
        print(f"Reason: {reason}")
        print(f"Time: {now_ist_iso()}")

        return True

    except Exception as e:
        print("Scaling failed:", e)
        return False



def callback(msg):
    try:
        event = json.loads(msg.data.decode())

        if event.get("severity") == "CRITICAL":
            print("Critical event:", event.get("reasons"))
            execute_scale("up", bypass_cooldown=True, reason="Critical event")
            msg.ack()
            return

        if "metrics" not in event:
            msg.ack()
            return

        m = event["metrics"]
        print(f"Metrics: CPU={m.get('cpu')} Lat={m.get('net_latency_ms')} Err={m.get('error_rate')}")

        metrics_window.add(event.get("timestamp", now_ist_iso()), m)
        stats = metrics_window.get_stats()

        print(f"Stats: CPU={stats['avg_cpu']} Mem={stats['avg_memory']} Err={stats['avg_error_rate']} Trend={stats['trend']}")

        action, reason = should_scale(stats)
        print("Decision:", reason)

        if action:
            mul = OOM_SCALE_MULTIPLIER if "OOM" in reason else NODE_FAILURE_SCALE_MULTIPLIER if "Node failure" in reason else 1
            execute_scale(action, multiplier=mul, reason=reason)

        msg.ack()

    except Exception as e:
        print("Message error:", e)
        msg.ack()


if __name__ == "__main__":
    start_background_threads()
    future = subscriber.subscribe(subscription_path, callback=callback)
    print("Autoscaler started")
    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()
        print("Autoscaler stopped")
