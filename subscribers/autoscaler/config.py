# OLD thresholds (kept for backward compatibility)
SCALE_UP_THRESHOLD = 75
SCALE_DOWN_THRESHOLD = 30

# NEW: Composite score thresholds (0-100 scale)
COMPOSITE_SCALE_UP = 70      # Scale up if composite > 70
COMPOSITE_SCALE_DOWN = 30    # Scale down if composite < 30

# Composite weights (must sum to 1.0)
WEIGHT_CPU = 0.4         # 40%
WEIGHT_LATENCY = 0.35    # 35%
WEIGHT_ERRORS = 0.25     # 25%

# Latency thresholds (milliseconds)
LATENCY_P95_THRESHOLD = 500   # p95 latency target
LATENCY_P99_THRESHOLD = 1000  # p99 latency critical

# Safety limits
MIN_REPLICAS = 2
MAX_REPLICAS = 8
COOLDOWN_SECONDS = 60
CRITICAL_COOLDOWN_SECONDS = 15  # For critical events

# Health check thresholds
MAX_CRASHLOOP_COUNT = 3  # Pod restart threshold before action
OOM_SCALE_MULTIPLIER = 2 # Scale by 2x on OOM

# Pub/Sub backlog thresholds
BACKLOG_SIZE_HIGH = 1000        # Scale up if > 1000 undelivered messages
OLDEST_MESSAGE_AGE_HIGH = 60    # Scale up if oldest message > 60 seconds
BACKLOG_GROWTH_RATE_HIGH = 50   # Scale up if growing > 50 msg/sec

# Node health thresholds
NODE_FAILURE_SCALE_MULTIPLIER = 1.5  # Scale by 1.5x on node failure
NODE_CAPACITY_LOSS_THRESHOLD = 0.25  # Scale if > 25% nodes down

# Kubernetes
DEPLOYMENT_NAME = "workload"
NAMESPACE = "default"