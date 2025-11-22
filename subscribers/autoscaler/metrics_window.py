from collections import deque

class MetricsWindow:
    def __init__(self, window_size=5):
        self.window_size = window_size 
        self.data = deque(maxlen=window_size)
    
    def add(self, timestamp, metrics):
        self.data.append({
            "timestamp": timestamp,
            "cpu": metrics.get("cpu", 0),
            "memory": metrics.get("memory", 0),
            "latency": metrics.get("net_latency_ms", 0),
            "error_rate": metrics.get("error_rate", 0)
        })
    
    def _calculate_percentile(self, values, percentile):
        if not values:
            return 0
        sorted_vals = sorted(values)
        index = int(len(sorted_vals) * percentile / 100)
        return sorted_vals[min(index, len(sorted_vals) - 1)]
    
    def get_stats(self):
        if not self.data:
            return {"count": 0}
        
        cpu_values = [d["cpu"] for d in self.data]
        mem_values = [d["memory"] for d in self.data]
        latency_values = [d["latency"] for d in self.data]
        error_values = [d["error_rate"] for d in self.data]
        
        avg_cpu = sum(cpu_values) / len(cpu_values)
        avg_mem = sum(mem_values) / len(mem_values)
        
        latency_p90 = self._calculate_percentile(latency_values, 90)
        latency_p95 = self._calculate_percentile(latency_values, 95)
        latency_p99 = self._calculate_percentile(latency_values, 99)
        
        avg_error_rate = sum(error_values) / len(error_values)
        
        # trend detection: last 30s vs last 5min
        if len(cpu_values) >= 4:
            recent_avg = sum(cpu_values[-2:]) / 2  # Last 30 sec (2 points)
            older_avg = sum(cpu_values[:-2]) / len(cpu_values[:-2])  # Older data
            
            if recent_avg > older_avg + 20:
                trend = "spiking"  # Instant spike detection
            elif recent_avg > older_avg + 10:
                trend = "increasing"
            elif recent_avg < older_avg - 10:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"
        
        return {
            "avg_cpu": round(avg_cpu, 2),
            "avg_memory": round(avg_mem, 2),
            "max_cpu": max(cpu_values),
            "latency_p90": round(latency_p90, 2),
            "latency_p95": round(latency_p95, 2),
            "latency_p99": round(latency_p99, 2),
            "avg_error_rate": round(avg_error_rate, 2),
            "trend": trend,
            "spike_detected": trend == "spiking",
            "count": len(self.data)
        }