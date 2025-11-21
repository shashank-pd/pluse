import datetime
from utils.ist import now_ist_dt, now_ist_iso
from kubernetes import client

class MemoryOptimizer:
    def __init__(self):
        self.apps_v1 = client.AppsV1Api()
        self.oom_history = {}  
        self.adjustment_history = {}  
        
        self.min_memory = "128Mi"
        self.max_memory = "2Gi"
        self.increment_factor = 1.5  # Increase by 50% on OOM
        self.cooldown_seconds = 300  # Wait 5 min between adjustments
        self.oom_threshold = 2  # Adjust after 2 OOMs
        
    def parse_memory(self, memory_str):
        if not memory_str:
            return 256 * 1024 * 1024  # Default 256Mi
        
        memory_str = str(memory_str).strip()
        
        if memory_str.endswith('Gi'):
            return int(float(memory_str[:-2]) * 1024 * 1024 * 1024)
        elif memory_str.endswith('Mi'):
            return int(float(memory_str[:-2]) * 1024 * 1024)
        elif memory_str.endswith('Ki'):
            return int(float(memory_str[:-2]) * 1024)
        else:
            return int(memory_str)
    
    def format_memory(self, bytes_value):
        if bytes_value >= 1024 * 1024 * 1024:
            return f"{int(bytes_value / (1024 * 1024 * 1024))}Gi"
        elif bytes_value >= 1024 * 1024:
            return f"{int(bytes_value / (1024 * 1024))}Mi"
        else:
            return f"{int(bytes_value / 1024)}Ki"
    
    def record_oom_event(self, pod_name):
        now = now_ist_dt()
        
        if pod_name not in self.oom_history:
            self.oom_history[pod_name] = {"count": 1, "last_seen": now}
        else:
            last_seen = self.oom_history[pod_name]["last_seen"]
            if (now - last_seen).total_seconds() > 3600:
                self.oom_history[pod_name] = {"count": 1, "last_seen": now}
            else:
                self.oom_history[pod_name]["count"] += 1
                self.oom_history[pod_name]["last_seen"] = now
        
        count = self.oom_history[pod_name]["count"]
        print(f"OOM event recorded for {pod_name} (count: {count})", flush=True)
        
        return count >= self.oom_threshold
    
    def should_adjust_memory(self, deployment_name, namespace):
        key = f"{namespace}/{deployment_name}"
        
        if key not in self.adjustment_history:
            return True
        
        last_adjustment = self.adjustment_history[key]["last_adjustment"]
        elapsed = (now_ist_dt() - last_adjustment).total_seconds()
        
        if elapsed < self.cooldown_seconds:
            print(f"Memory adjustment cooldown: {int(self.cooldown_seconds - elapsed)}s remaining", flush=True)
            return False
        
        return True
    
    def adjust_memory_limits(self, deployment_name, namespace):
        try:
            deployment = self.apps_v1.read_namespaced_deployment(deployment_name, namespace)
            containers = deployment.spec.template.spec.containers
            if not containers:
                print(f"No containers found in deployment {deployment_name}", flush=True)
                return False, None, None
            
            container = containers[0]  
            current_limit = None
            
            if container.resources and container.resources.limits:
                current_limit = container.resources.limits.get("memory")
            
            if not current_limit:
                current_limit = "256Mi"  
            
            current_bytes = self.parse_memory(current_limit)
            new_bytes = int(current_bytes * self.increment_factor)
            
            max_bytes = self.parse_memory(self.max_memory)
            if new_bytes > max_bytes:
                new_bytes = max_bytes
                print(f"Memory limit capped at maximum: {self.max_memory}", flush=True)
            
            new_limit = self.format_memory(new_bytes)
            
            if current_bytes >= max_bytes:
                print(f"Already at maximum memory limit: {current_limit}", flush=True)
                return False, current_limit, current_limit
            
            if not container.resources:
                container.resources = client.V1ResourceRequirements()
            if not container.resources.limits:
                container.resources.limits = {}
            if not container.resources.requests:
                container.resources.requests = {}
            
            container.resources.limits["memory"] = new_limit
            container.resources.requests["memory"] = new_limit  
            
            self.apps_v1.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=deployment
            )
            
            key = f"{namespace}/{deployment_name}"
            self.adjustment_history[key] = {
                "last_adjustment": now_ist_dt(),
                "current_limit": new_limit
            }
            
            print(f"Memory limit adjusted: {current_limit} → {new_limit}", flush=True)
            print(f"Pods will restart with new memory limits", flush=True)
            
            return True, current_limit, new_limit
            
        except Exception as e:
            print(f"Failed to adjust memory limits: {e}", flush=True)
            return False, None, None
    
    def get_current_memory_limit(self, deployment_name, namespace):
        try:
            deployment = self.apps_v1.read_namespaced_deployment(deployment_name, namespace)
            containers = deployment.spec.template.spec.containers
            
            if containers and containers[0].resources and containers[0].resources.limits:
                return containers[0].resources.limits.get("memory", "256Mi")
            
            return "256Mi"
        except Exception as e:
            print(f"Failed to get memory limit: {e}", flush=True)
            return "256Mi"
