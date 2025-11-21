from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from kubernetes import client, config
from google.cloud import pubsub_v1
import os
import json
import time
from datetime import datetime, timedelta
from ist import now_ist_iso, now_ist_dt, to_ist_iso
from collections import deque
import threading

app = Flask(__name__)
CORS(app)

PROJECT_ID = os.getenv("PROJECT_ID", "pulse-477304")
TOPIC_NAME = os.getenv("TOPIC_NAME", "events-topic")

try:
    config.load_incluster_config()
except:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_NAME)

metrics_history = deque(maxlen=100)
decisions_history = deque(maxlen=50)

def get_current_status():
    try:
        workload = apps_v1.read_namespaced_deployment("workload", "default")
        replicas = workload.spec.replicas
        ready_replicas = workload.status.ready_replicas or 0
        
        nodes = v1.list_node()
        node_count = len([n for n in nodes.items if 'node-role.kubernetes.io/master' not in n.metadata.labels])
        ready_nodes = len([n for n in nodes.items if any(c.type == "Ready" and c.status == "True" for c in n.status.conditions)])
        
        pods = v1.list_namespaced_pod("default", label_selector="app=pulse-autoscaler")
        autoscaler_status = "Running" if pods.items and pods.items[0].status.phase == "Running" else "Not Running"
        
        last_decision = get_last_decision_from_logs()
        
        return {
            "timestamp": now_ist_iso(),
            "replicas": replicas,
            "ready_replicas": ready_replicas,
            "nodes": node_count,
            "ready_nodes": ready_nodes,
            "autoscaler_status": autoscaler_status,
            "last_decision": last_decision
        }
    except Exception as e:
        return {"error": str(e)}

def get_last_decision_from_logs():
    try:
        pods = v1.list_namespaced_pod("default", label_selector="app=pulse-autoscaler")
        if not pods.items:
            return None
        
        pod_name = pods.items[0].metadata.name
        logs = v1.read_namespaced_pod_log(pod_name, "default", tail_lines=100)
        
        for line in reversed(logs.split('\n')):
            if 'Decision:' in line or 'Scaled' in line:
                return line.strip()
        
        return "No recent decisions"
    except:
        return "Unable to fetch logs"

def get_metrics_history(minutes=10):
    cutoff = now_ist_dt() - timedelta(minutes=minutes)
    results = []
    for m in metrics_history:
        try:
            dt = datetime.fromisoformat(m['timestamp'])
        except Exception:
            try:
                dt = datetime.fromisoformat(to_ist_iso(m['timestamp']))
            except Exception:
                continue
        if dt > cutoff:
            results.append(m)
    return results

def get_autoscaler_logs(lines=50):
    try:
        pods = v1.list_namespaced_pod("default", label_selector="app=pulse-autoscaler")
        if not pods.items:
            return ["No autoscaler pod found"]
        
        pod_name = pods.items[0].metadata.name
        logs = v1.read_namespaced_pod_log(pod_name, "default", tail_lines=lines)
        return logs.split('\n')
    except Exception as e:
        return [f"Error fetching logs: {e}"]

def publish_test_metric(cpu, memory, latency, error_rate, severity="INFO"):
    try:
        reasons = []
        if severity == "CRITICAL":
            if cpu > 90:
                reasons.append(f"cpu>{cpu}%")
            if memory > 90:
                reasons.append(f"mem>{memory}%")
            if latency > 500:
                reasons.append(f"latency>{latency}ms")
            if error_rate > 5:
                reasons.append(f"errors>{error_rate}%")
        
        message = {
            "source": "dashboard",
            "severity": severity,
            "reasons": reasons,
            "metrics": {
                "cpu": cpu,
                "memory": memory,
                "net_latency_ms": latency,
                "error_rate": error_rate
            },
            "timestamp": now_ist_iso()
        }
        
        future = publisher.publish(topic_path, json.dumps(message).encode('utf-8'))
        future.result()
        
        metrics_history.append({
            "timestamp": message["timestamp"],
            "cpu": cpu,
            "memory": memory,
            "latency": latency,
            "error_rate": error_rate,
            "severity": severity
        })
        
        return True
    except Exception as e:
        print(f"Error publishing metric: {e}")
        return False


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    return jsonify(get_current_status())

@app.route('/api/metrics')
def api_metrics():
    minutes = request.args.get('minutes', 10, type=int)
    return jsonify(get_metrics_history(minutes))

@app.route('/api/decisions')
def api_decisions():
    return jsonify(list(decisions_history))

@app.route('/api/logs')
def api_logs():
    lines = request.args.get('lines', 50, type=int)
    return jsonify({"logs": get_autoscaler_logs(lines)})

@app.route('/api/nodes')
def api_nodes():
    try:
        nodes = v1.list_node()
        node_list = []
        for node in nodes.items:
            if 'node-role.kubernetes.io/master' in node.metadata.labels:
                continue
            
            status = "Ready" if any(c.type == "Ready" and c.status == "True" for c in node.status.conditions) else "NotReady"
            
            node_list.append({
                "name": node.metadata.name,
                "status": status,
                "cpu": node.status.allocatable.get('cpu', 'N/A'),
                "memory": node.status.allocatable.get('memory', 'N/A'),
                "created": node.metadata.creation_timestamp.isoformat()
            })
        
        return jsonify({"nodes": node_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/pods')
def api_pods():
    try:
        pods = v1.list_namespaced_pod("default", label_selector="app=workload")
        pod_list = []
        
        for pod in pods.items:
            pod_list.append({
                "name": pod.metadata.name,
                "status": pod.status.phase,
                "node": pod.spec.node_name,
                "restarts": sum(c.restart_count for c in pod.status.container_statuses or []),
                "created": pod.metadata.creation_timestamp.isoformat()
            })
        
        return jsonify({"pods": pod_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/scale_up', methods=['POST'])
def test_scale_up():
    try:
        initial = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        for i in range(3):
            publish_test_metric(85 + i*2, 65 + i*5, 480 + i*10, 6.0 + i*0.5, "WARNING")
            time.sleep(3)
        
        time.sleep(15)
        
        final = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        result = {
            "test": "Scale UP",
            "initial_replicas": initial,
            "final_replicas": final,
            "scaled": final > initial,
            "message": f"Scaled from {initial} to {final} replicas" if final > initial else "No scaling occurred"
        }
        
        decisions_history.append({
            "timestamp": now_ist_iso(),
            "test": "Scale UP",
            "result": result["message"]
        })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/scale_down', methods=['POST'])
def test_scale_down():
    try:
        initial = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        for i in range(4):
            publish_test_metric(20 - i*2, 30 - i*2, 100 - i*5, 1.0 - i*0.2, "INFO")
            time.sleep(3)
        
        time.sleep(70)
        
        final = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        result = {
            "test": "Scale DOWN",
            "initial_replicas": initial,
            "final_replicas": final,
            "scaled": final < initial,
            "message": f"Scaled from {initial} to {final} replicas" if final < initial else "No scaling (may be at minimum)"
        }
        
        decisions_history.append({
            "timestamp": now_ist_iso(),
            "test": "Scale DOWN",
            "result": result["message"]
        })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/balanced', methods=['POST'])
def test_balanced():
    try:
        initial = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        for i in range(3):
            publish_test_metric(50 + i, 45 + i, 250 + i*10, 3.0, "INFO")
            time.sleep(3)
        
        time.sleep(20)
        
        final = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        result = {
            "test": "Balanced Load",
            "initial_replicas": initial,
            "final_replicas": final,
            "scaled": final != initial,
            "message": f"No scaling expected (balanced load). Replicas: {final}"
        }
        
        decisions_history.append({
            "timestamp": now_ist_iso(),
            "test": "Balanced Load",
            "result": result["message"]
        })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/critical', methods=['POST'])
def test_critical():
    try:
        initial = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        publish_test_metric(92, 91, 550, 7.0, "CRITICAL")
        
        time.sleep(20)
        
        final = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        result = {
            "test": "Critical Event",
            "initial_replicas": initial,
            "final_replicas": final,
            "scaled": final > initial,
            "message": f"Critical event: Scaled from {initial} to {final} replicas (bypassed cooldown)"
        }
        
        decisions_history.append({
            "timestamp": now_ist_iso(),
            "test": "Critical Event",
            "result": result["message"]
        })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/high_latency', methods=['POST'])
def test_high_latency():
    try:
        initial = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        for i in range(3):
            publish_test_metric(40 + i, 35 + i, 600 + i*20, 1.0, "WARNING")
            time.sleep(3)
        
        time.sleep(20)
        
        final = apps_v1.read_namespaced_deployment("workload", "default").spec.replicas
        
        result = {
            "test": "High Latency",
            "initial_replicas": initial,
            "final_replicas": final,
            "scaled": final > initial,
            "message": f"High latency test: Scaled from {initial} to {final} replicas"
        }
        
        decisions_history.append({
            "timestamp": now_ist_iso(),
            "test": "High Latency",
            "result": result["message"]
        })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trigger_load', methods=['POST'])
def trigger_load():
    data = request.json
    cpu = data.get('cpu', 80)
    memory = data.get('memory', 60)
    latency = data.get('latency', 400)
    error_rate = data.get('error_rate', 5)
    severity = data.get('severity', 'WARNING')
    
    success = publish_test_metric(cpu, memory, latency, error_rate, severity)
    
    if success:
        return jsonify({"status": "success", "message": f"Published metric: CPU={cpu}%, Latency={latency}ms"})
    else:
        return jsonify({"status": "error", "message": "Failed to publish metric"}), 500

def background_monitor():
    while True:
        try:
            status = get_current_status()
            if 'error' not in status:
                metrics_history.append({
                    "timestamp": status["timestamp"],
                    "replicas": status["replicas"],
                    "nodes": status["nodes"],
                    "ready_replicas": status["ready_replicas"]
                })
        except:
            pass
        
        time.sleep(30)

monitor_thread = threading.Thread(target=background_monitor, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
