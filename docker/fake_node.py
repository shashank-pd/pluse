import time, random, json, os
from google.cloud import pubsub_v1

PROJECT = os.environ.get("GCP_PROJECT")
TOPIC = os.environ.get("METRICS_TOPIC","metrics-topic")
NODE_ID = os.environ.get("NODE_ID","node-1")

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT, TOPIC)

def publish_metric():
    cpu = random.randint(5,95)
    mem = random.randint(10,95)
    disk = random.randint(20,95)
    net_lat = random.randint(10,500)  # ms
    proc = random.randint(50,300)
    load = round(random.random()*4,2)
    error_rate = round(random.random() * 10, 2)  # 0-10% error rate
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "node_id": NODE_ID,
        "timestamp": ts,
        "metrics": {
            "cpu": cpu,
            "memory": mem,
            "disk": disk,
            "net_latency_ms": net_lat,
            "process_count": proc,
            "load_avg": load,
            "error_rate": error_rate
        }
    }
    data = json.dumps(payload).encode("utf-8")
    publisher.publish(topic_path, data)
    print("Published", payload)

if __name__ == "__main__":
    while True:
        publish_metric()
        # occasionally publish an ERROR log message
        if random.random() < 0.05:
            err = {"node_id": NODE_ID, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "log": "CRITICAL: simulated service crash", "sev": "CRITICAL"}
            publisher.publish(topic_path, json.dumps(err).encode("utf-8"))
            print("Published error", err)
        time.sleep(int(os.environ.get("METRIC_INTERVAL", "5")))
