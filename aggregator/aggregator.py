import base64, json, os, datetime
from utils.ist import now_ist_iso
from google.cloud import pubsub_v1, logging_v2
from flask import Flask, request

# ENV VARS
PROJECT = os.environ["GCP_PROJECT"]
EVENT_TOPIC = os.environ.get("EVENT_TOPIC", "events-topic")

# Pub/Sub Publisher
publisher = pubsub_v1.PublisherClient()
event_path = publisher.topic_path(PROJECT, EVENT_TOPIC)

# Cloud Logging client
logging_client = logging_v2.Client()
logger = logging_client.logger("aggregator-events")

app = Flask(__name__)

def classify_event(data):
    event = {
        "timestamp": now_ist_iso(),
        "source": "aggregator",
        "node_id": data.get("node_id", "unknown"),
        "event_type": "metrics_event",
    }

    # If metrics exist → classify
    if "metrics" in data:
        m = data["metrics"]
        cpu = m.get("cpu", 0)
        mem = m.get("memory", 0)
        error_rate = m.get("error_rate", 0)
        latency = m.get("net_latency_ms", 0)

        severity = "INFO"
        reasons = []

        if cpu > 90:
            severity = "CRITICAL"
            reasons.append(f"cpu>{cpu}")
        elif cpu > 75:
            severity = "WARNING"
            reasons.append(f"cpu>{cpu}")

        if mem > 90:
            severity = "CRITICAL"
            reasons.append(f"mem>{mem}")
        
        if error_rate > 8:
            severity = "CRITICAL"
            reasons.append(f"errors>{error_rate}%")
        elif error_rate > 5:
            if severity == "INFO":
                severity = "WARNING"
            reasons.append(f"errors>{error_rate}%")
        
        if latency > 400:
            if severity == "INFO":
                severity = "WARNING"
            reasons.append(f"latency>{latency}ms")

        event["severity"] = severity
        event["reasons"] = reasons
        event["metrics"] = m
        return event #return none if missing

    # Otherwise treat as log event
    log_msg = data.get("log")
    sev = "CRITICAL" if "CRITICAL" in log_msg else "ERROR"

    event["severity"] = sev
    event["event_type"] = "log_event"
    event["log"] = log_msg
    return event


def publish_event(event):
    publisher.publish(event_path, json.dumps(event).encode("utf-8"))
    logger.log_text(json.dumps(event), severity=event["severity"])
    print("PUBLISHED EVENT:", event)


@app.route("/", methods=["POST"])
def index():
    body = request.get_json(silent=True)
    if not body:
        return ("", 400)

    pubsub_message = body.get("message")
    if not pubsub_message:
        return ("", 400)

    payload = base64.b64decode(pubsub_message["data"]).decode("utf-8")

    try:
        obj = json.loads(payload)
    except:
        obj = {"log": payload}

    event = classify_event(obj)
    print("RECEIVED:", event)
    publish_event(event)

    return ("", 204)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
