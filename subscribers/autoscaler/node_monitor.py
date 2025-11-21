import datetime
from utils.ist import now_ist_dt, now_ist_iso
from kubernetes import client

class NodeMonitor:
    def __init__(self):
        self.core_v1 = client.CoreV1Api()
        self.unhealthy_nodes = {}
        self.quarantined_nodes = set()
        self.quarantine_threshold = 300  # 5 minutes
        
    def check_node_health(self):
        try:
            nodes = self.core_v1.list_node()
            not_ready_nodes = []
            healthy_count = 0
            
            for node in nodes.items:
                node_name = node.metadata.name
                is_ready = False
                is_schedulable = not node.spec.unschedulable if node.spec.unschedulable is not None else True
                
                for condition in node.status.conditions:
                    if condition.type == "Ready":
                        if condition.status == "True":
                            is_ready = True
                        break
                
                cordoned_by_scaler = False
                if node.spec.taints:
                    for taint in node.spec.taints:
                        if taint.key == "node-scaler.pulse/draining":
                            cordoned_by_scaler = True
                            break
                
                if cordoned_by_scaler:
                    node_healthy = True  
                elif not is_ready:
                    node_healthy = False 
                elif not is_schedulable:
                    node_healthy = False  
                else:
                    node_healthy = True  
                
                if node_healthy:
                    healthy_count += 1
                    if node_name in self.unhealthy_nodes:
                        del self.unhealthy_nodes[node_name]
                        print(f"Node {node_name} recovered", flush=True)
                    
                    if node_name in self.quarantined_nodes:
                        self._unquarantine_node(node_name)
                else:
                    not_ready_nodes.append(node_name)
                    
                    if node_name not in self.unhealthy_nodes:
                        self.unhealthy_nodes[node_name] = now_ist_dt()
                        status = "NOT READY" if not is_ready else "CORDONED"
                        print(f"Node {node_name} is {status}", flush=True)
                
                if not is_ready and node_name in self.unhealthy_nodes:
                    elapsed = (now_ist_dt() - self.unhealthy_nodes[node_name]).total_seconds()
                    if elapsed > self.quarantine_threshold and node_name not in self.quarantined_nodes:
                        self._quarantine_node(node_name)
                        print(f"Quarantined node: {node_name} (unhealthy for {int(elapsed)}s)", flush=True)
            
            return {
                "not_ready_nodes": not_ready_nodes,
                "quarantined_nodes": list(self.quarantined_nodes),
                "total_nodes": len(nodes.items),
                "healthy_nodes": healthy_count
            }
            
        except Exception as e:
            print(f"Node health check failed: {e}", flush=True)
            return {
                "not_ready_nodes": [],
                "quarantined_nodes": [],
                "total_nodes": 0,
                "healthy_nodes": 0
            }
    
    def _quarantine_node(self, node_name):
        try:
            body = {
                "spec": {
                    "unschedulable": True
                }
            }
            self.core_v1.patch_node(node_name, body)
            self.quarantined_nodes.add(node_name)
            print(f"Quarantined node: {node_name} (not-ready for {self.quarantine_threshold}s)", flush=True)
            return True
        except Exception as e:
            print(f"Failed to quarantine {node_name}: {e}", flush=True)
            return False
    
    def _unquarantine_node(self, node_name):
        try:
            body = {
                "spec": {
                    "unschedulable": False
                }
            }
            self.core_v1.patch_node(node_name, body)
            self.quarantined_nodes.discard(node_name)
            print(f"Unquarantined node: {node_name} (recovered)", flush=True)
            return True
        except Exception as e:
            print(f"Failed to unquarantine {node_name}: {e}", flush=True)
            return False
    
    def get_node_capacity_loss(self):
        try:
            nodes = self.core_v1.list_node()
            total_nodes = len(nodes.items)
            
            if total_nodes == 0:
                return 0.0
            
            not_ready_count = len(self.unhealthy_nodes)
            return not_ready_count / total_nodes
            
        except Exception as e:
            print(f"Capacity calculation failed: {e}", flush=True)
            return 0.0
