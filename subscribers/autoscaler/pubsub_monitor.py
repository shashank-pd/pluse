import os
import time
from google.cloud import monitoring_v3
from google.api_core import exceptions

class PubSubMonitor:
    def __init__(self, project_id, subscription_id):
        self.project_id = project_id
        self.subscription_id = subscription_id
        self.client = monitoring_v3.MetricServiceClient()
        self.project_name = f"projects/{project_id}"
        
    def get_backlog_stats(self):
        try:
            backlog_size = self._query_metric(
                "pubsub.googleapis.com/subscription/num_undelivered_messages"
            )
            
            oldest_age = self._query_metric(
                "pubsub.googleapis.com/subscription/oldest_unacked_message_age"
            )
            
            return {
                "backlog_size": int(backlog_size) if backlog_size else 0,
                "oldest_message_age": int(oldest_age) if oldest_age else 0
            }
        except Exception as e:
            print(f"Pub/Sub monitoring failed: {e}", flush=True)
            return {"backlog_size": 0, "oldest_message_age": 0}
    
    def _query_metric(self, metric_type):
        try:
            now = time.time()
            interval = monitoring_v3.TimeInterval(
                {
                    "end_time": {"seconds": int(now)},
                    "start_time": {"seconds": int(now - 300)},  # Last 5 min
                }
            )
            filter_str = (
                f'metric.type = "{metric_type}" '
                f'AND resource.labels.subscription_id = "{self.subscription_id}"'
            )
            
            results = self.client.list_time_series(
                request={
                    "name": self.project_name,
                    "filter": filter_str,
                    "interval": interval,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }
            )
            
            for result in results:
                if result.points:
                    point = result.points[0]
                    if point.value.int64_value:
                        return point.value.int64_value
                    elif point.value.double_value:
                        return point.value.double_value
            
            return 0
            
        except exceptions.NotFound:
            return 0
        except Exception as e:
            print(f"Metric query failed for {metric_type}: {e}", flush=True)
            return 0
