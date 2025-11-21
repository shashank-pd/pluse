
import datetime
from utils.ist import now_ist_dt, now_ist_iso
import time
from kubernetes import client
from kubernetes.client.rest import ApiException
from google.cloud import container_v1

class NodeScaler:
    def __init__(self, project_id, zone, cluster_name, node_pool_name):
        self.project_id = project_id
        self.zone = zone
        self.cluster_name = cluster_name
        self.node_pool_name = node_pool_name
        
        try:
            self.gke_client = container_v1.ClusterManagerClient()
            self.cluster_path = f"projects/{project_id}/locations/{zone}/clusters/{cluster_name}"
            self.node_pool_path = f"{self.cluster_path}/nodePools/{node_pool_name}"
        except Exception as e:
            print(f"GKE client initialization warning: {e}", flush=True)
            self.gke_client = None
        
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        
        self.min_nodes = 1
        self.max_nodes = 5
        self.scale_up_threshold = 0.80  
        self.scale_down_threshold = 0.35  
        self.cooldown_minutes = 3  
        
        self.last_scale_action = None
        self.last_scale_time = None
        self.scale_history = []  
        
        print("Node Scaler initialized", flush=True)
        print(f"   Cluster: {cluster_name} (Zone: {zone})", flush=True)
        print(f"   Node Pool: {node_pool_name}", flush=True)
        print(f"   Range: {self.min_nodes}-{self.max_nodes} nodes", flush=True)
        print(f"   Scale-up threshold: {self.scale_up_threshold*100}%", flush=True)
        print(f"   Scale-down threshold: {self.scale_down_threshold*100}%", flush=True)
    
    def get_current_node_count(self):
        try:
            nodes = self.core_v1.list_node()
            ready_nodes = [n for n in nodes.items if self._is_node_ready(n)]
            return len(ready_nodes)
        except Exception as e:
            print(f"Failed to get node count: {e}", flush=True)
            return 0
    
    def _is_node_ready(self, node):
        for condition in node.status.conditions:
            if condition.type == "Ready":
                return condition.status == "True"
        return False
    
    def get_node_metrics(self):
        try:
            from kubernetes import client as k8s_client
            
            custom_api = k8s_client.CustomObjectsApi()
            node_metrics_raw = custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes"
            )
            
            actual_usage = {}
            for item in node_metrics_raw.get('items', []):
                node_name = item['metadata']['name']
                usage = item['usage']
                actual_usage[node_name] = {
                    'cpu': self._parse_cpu(usage.get('cpu', '0')),
                    'memory': self._parse_memory(usage.get('memory', '0'))
                }
            
            nodes = self.core_v1.list_node()
            node_metrics = []
            total_pods = 0
            
            for node in nodes.items:
                if not self._is_node_ready(node):
                    continue
                
                node_name = node.metadata.name
                
                allocatable = node.status.allocatable
                cpu_allocatable = self._parse_cpu(allocatable.get('cpu', '0'))
                memory_allocatable = self._parse_memory(allocatable.get('memory', '0'))
                
                node_usage = actual_usage.get(node_name, {'cpu': 0, 'memory': 0})
                cpu_used = node_usage['cpu']
                memory_used = node_usage['memory']
                
                pods = self.core_v1.list_pod_for_all_namespaces(
                    field_selector=f"spec.nodeName={node_name}"
                )
                pod_count = len([p for p in pods.items if p.status.phase in ["Running", "Pending"]])
                total_pods += pod_count
                
                cpu_percent = (cpu_used / cpu_allocatable * 100) if cpu_allocatable > 0 else 0
                memory_percent = (memory_used / memory_allocatable * 100) if memory_allocatable > 0 else 0
                
                node_metrics.append({
                    'name': node_name,
                    'cpu_allocatable_m': cpu_allocatable,
                    'cpu_used_m': cpu_used,
                    'cpu_percent': cpu_percent,
                    'memory_allocatable_mb': memory_allocatable / (1024*1024),
                    'memory_used_mb': memory_used / (1024*1024),
                    'memory_percent': memory_percent,
                    'pod_count': pod_count
                })
            
            if node_metrics:
                avg_cpu = sum(n['cpu_percent'] for n in node_metrics) / len(node_metrics)
                avg_memory = sum(n['memory_percent'] for n in node_metrics) / len(node_metrics)
                
                return {
                    'avg_cpu_percent': avg_cpu,
                    'avg_memory_percent': avg_memory,
                    'node_count': len(node_metrics),
                    'nodes': node_metrics,
                    'total_pods': total_pods
                }
            
            return {
                'avg_cpu_percent': 0,
                'avg_memory_percent': 0,
                'node_count': 0,
                'nodes': [],
                'total_pods': 0
            }
            
        except Exception as e:
            print(f"Failed to get node metrics: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return {
                'avg_cpu_percent': 0,
                'avg_memory_percent': 0,
                'node_count': 0,
                'nodes': [],
                'total_pods': 0
            }
    
    def get_unschedulable_pods(self):
        try:
            pods = self.core_v1.list_pod_for_all_namespaces()
            unschedulable = []
            
            for pod in pods.items:
                if pod.status.phase == "Pending":
                    for condition in pod.status.conditions or []:
                        if condition.reason == "Unschedulable":
                            if "Insufficient" in condition.message or "insufficient" in condition.message:
                                unschedulable.append({
                                    'name': pod.metadata.name,
                                    'namespace': pod.metadata.namespace,
                                    'message': condition.message
                                })
                                break
            
            return unschedulable
            
        except Exception as e:
            print(f"Failed to get unschedulable pods: {e}", flush=True)
            return []
    
    def should_scale_up(self, metrics, unschedulable_pods):
        current_nodes = metrics['node_count']
        
        if current_nodes >= self.max_nodes:
            return False, f"Already at max nodes ({self.max_nodes})"
        
        if not self._cooldown_expired():
            remaining = self._cooldown_remaining()
            return False, f"In cooldown period ({remaining}s remaining)"
        
        if unschedulable_pods:
            pod_names = [p['name'] for p in unschedulable_pods[:3]]
            return True, f"CRITICAL: {len(unschedulable_pods)} unschedulable pods ({', '.join(pod_names)}...)"
        
        if metrics['avg_cpu_percent'] > self.scale_up_threshold * 100:
            return True, f"High CPU utilization: {metrics['avg_cpu_percent']:.1f}% > {self.scale_up_threshold*100}%"
        
        if metrics['avg_memory_percent'] > self.scale_up_threshold * 100:
            return True, f"High memory utilization: {metrics['avg_memory_percent']:.1f}% > {self.scale_up_threshold*100}%"
        
        for node in metrics['nodes']:
            if node['cpu_percent'] > 90:
                return True, f"Node {node['name']} overloaded: {node['cpu_percent']:.1f}% CPU"
        
        return False, "Cluster utilization within normal range"
    
    def should_scale_down(self, metrics):
        current_nodes = metrics['node_count']
        
        if current_nodes <= self.min_nodes:
            return False, f"Already at min nodes ({self.min_nodes})", None
        
        if not self._cooldown_expired():
            remaining = self._cooldown_remaining()
            return False, f"In cooldown period ({remaining}s remaining)", None
        
        if metrics['avg_cpu_percent'] > self.scale_down_threshold * 100:
            return False, f"CPU still utilized: {metrics['avg_cpu_percent']:.1f}%", None
        
        if metrics['avg_memory_percent'] > self.scale_down_threshold * 100:
            return False, f"Memory still utilized: {metrics['avg_memory_percent']:.1f}%", None
        
        if metrics['nodes'] and len(metrics['nodes']) > 1:
            sorted_nodes = sorted(metrics['nodes'], key=lambda x: x['cpu_percent'])
            least_utilized = sorted_nodes[0]
            
            if least_utilized['cpu_percent'] < self.scale_down_threshold * 100:
                return True, \
                       f"Node {least_utilized['name']} underutilized: CPU={least_utilized['cpu_percent']:.1f}%, Pods={least_utilized['pod_count']}", \
                       least_utilized['name']
        
        return False, "No nodes suitable for removal", None
    
    def scale_up(self, reason):
        try:
            current_nodes = self.get_current_node_count()
            target_nodes = current_nodes + 1
            
            print(f"", flush=True)
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            print(f"SCALING UP NODES (Custom Node Scaler)", flush=True)
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            print(f"Reason: {reason}", flush=True)
            print(f"Action: {current_nodes} → {target_nodes} nodes", flush=True)
            print(f"Time: {now_ist_iso()}", flush=True)
            
            if not self.gke_client:
                print(f"GKE client not available (simulating scale-up)", flush=True)
                self._record_scale_action("scale_up", current_nodes, target_nodes, reason)
                return True
            
            request = container_v1.SetNodePoolSizeRequest(
                name=self.node_pool_path,
                node_count=target_nodes
            )
            
            operation = self.gke_client.set_node_pool_size(request=request)
            
            print(f"Scale-up initiated successfully", flush=True)
            print(f"   Operation: {operation.name}", flush=True)
            print(f"   Expected completion: 1-2 minutes", flush=True)
            
            self._record_scale_action("scale_up", current_nodes, target_nodes, reason)
            
            return True
            
        except Exception as e:
            print(f"Failed to scale up nodes: {e}", flush=True)
            return False
    
    def scale_down(self, reason, node_name):
        try:
            current_nodes = self.get_current_node_count()
            target_nodes = current_nodes - 1
            
            print(f"", flush=True)
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            print(f"SCALING DOWN NODES (Custom Node Scaler)", flush=True)
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            print(f"Reason: {reason}", flush=True)
            print(f"Action: {current_nodes} → {target_nodes} nodes", flush=True)
            print(f"Target node: {node_name}", flush=True)
            print(f"Time: {now_ist_iso()}", flush=True)
            
            print(f"Step 1/3: Cordoning node...", flush=True)
            self._cordon_node(node_name)
            self._mark_node_draining(node_name)
            
            print(f"Step 2/3: Draining pods from node...", flush=True)
            evicted_count = self._drain_node(node_name)
            print(f"   Evicted {evicted_count} pods", flush=True)
            
            print(f"Waiting 30s for pods to reschedule...", flush=True)
            time.sleep(30)
            
            if not self.gke_client:
                print(f"GKE client not available (simulating scale-down)", flush=True)
                self._record_scale_action("scale_down", current_nodes, target_nodes, reason)
                return True
            
            print(f"Step 3/3: Resizing node pool...", flush=True)
            request = container_v1.SetNodePoolSizeRequest(
                name=self.node_pool_path,
                node_count=target_nodes
            )
            
            operation = self.gke_client.set_node_pool_size(request=request)
            
            print(f"    Scale-down initiated successfully", flush=True)
            print(f"   Operation: {operation.name}", flush=True)
            print(f"   Expected completion: 1-2 minutes", flush=True)
            
            self._record_scale_action("scale_down", current_nodes, target_nodes, reason)
            
            return True
            
        except Exception as e:
            print(f"Failed to scale down nodes: {e}", flush=True)
            return False
    
    def _cordon_node(self, node_name):
        try:
            body = {"spec": {"unschedulable": True}}
            self.core_v1.patch_node(node_name, body)
            print(f"   Node marked unschedulable", flush=True)
        except Exception as e:
            print(f"   Failed to cordon node: {e}", flush=True)
    
    def _mark_node_draining(self, node_name):
        try:
            from kubernetes.client import V1Taint
            taint = V1Taint(
                key="node-scaler.pulse/draining",
                value="true",
                effect="NoSchedule"
            )
            node = self.core_v1.read_node(node_name)
            if not node.spec.taints:
                node.spec.taints = []
            node.spec.taints.append(taint)
            self.core_v1.patch_node(node_name, {"spec": {"taints": node.spec.taints}})
            print(f"    Node marked for scale-down (taint added)", flush=True)
        except Exception as e:
            print(f"    Failed to add draining taint: {e}", flush=True)
    
    def _drain_node(self, node_name):
        evicted_count = 0
        try:
            pods = self.core_v1.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node_name}"
            )
            
            for pod in pods.items:
                if self._is_daemonset_pod(pod):
                    continue
                
                if pod.metadata.namespace == "kube-system":
                    continue
                
                try:
                    self.core_v1.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        grace_period_seconds=30
                    )
                    evicted_count += 1
                    print(f"   Evicted: {pod.metadata.namespace}/{pod.metadata.name}", flush=True)
                except Exception as e:
                    print(f"   Failed to evict {pod.metadata.name}: {e}", flush=True)
            
            return evicted_count
                    
        except Exception as e:
            print(f"   Failed during node drain: {e}", flush=True)
            return evicted_count
    
    
    def _is_daemonset_pod(self, pod):
        if pod.metadata.owner_references:
            for ref in pod.metadata.owner_references:
                if ref.kind == "DaemonSet":
                    return True
        return False
    
    def _cooldown_expired(self):
        if self.last_scale_time is None:
            return True
        
        elapsed_seconds = (now_ist_dt() - self.last_scale_time).total_seconds()
        return elapsed_seconds >= (self.cooldown_minutes * 60)
    
    def _cooldown_remaining(self):
        if self.last_scale_time is None:
            return 0
        
        elapsed_seconds = (now_ist_dt() - self.last_scale_time).total_seconds()
        cooldown_seconds = self.cooldown_minutes * 60
        remaining = cooldown_seconds - elapsed_seconds
        return max(0, int(remaining))
    
    def _record_scale_action(self, action, old_count, new_count, reason):
        self.last_scale_action = action
        self.last_scale_time = now_ist_dt()
        
        self.scale_history.append({
            'timestamp': self.last_scale_time,
            'action': action,
            'old_count': old_count,
            'new_count': new_count,
            'reason': reason
        })
        
        if len(self.scale_history) > 50:
            self.scale_history = self.scale_history[-50:]
    
    def _parse_cpu(self, cpu_str):
        if not cpu_str:
            return 0
        cpu_str = str(cpu_str).strip()
        if cpu_str.endswith('n'):
            return int(cpu_str[:-1]) / 1000000
        elif cpu_str.endswith('m'):
            return int(cpu_str[:-1])
        else:
            return int(float(cpu_str) * 1000)
    
    def _parse_memory(self, memory_str):
        if not memory_str:
            return 0
        
        memory_str = str(memory_str).strip()
        
        if memory_str.endswith('Gi'):
            return int(float(memory_str[:-2]) * 1024 * 1024 * 1024)
        elif memory_str.endswith('Mi'):
            return int(float(memory_str[:-2]) * 1024 * 1024)
        elif memory_str.endswith('Ki'):
            return int(float(memory_str[:-2]) * 1024)
        elif memory_str.endswith('G'):
            return int(float(memory_str[:-1]) * 1000 * 1000 * 1000)
        elif memory_str.endswith('M'):
            return int(float(memory_str[:-1]) * 1000 * 1000)
        elif memory_str.endswith('K'):
            return int(float(memory_str[:-1]) * 1000)
        else:
            return int(memory_str)
    
    def check_and_scale(self):
        try:
            metrics = self.get_node_metrics()
            unschedulable_pods = self.get_unschedulable_pods()
            
            print(f"", flush=True)
            print(f"    Node Scaler Health Check", flush=True)
            print(f"   Nodes: {metrics['node_count']}/{self.max_nodes} (min: {self.min_nodes})", flush=True)
            print(f"   Avg CPU: {metrics['avg_cpu_percent']:.1f}%", flush=True)
            print(f"   Avg Memory: {metrics['avg_memory_percent']:.1f}%", flush=True)
            print(f"   Total Pods: {metrics['total_pods']}", flush=True)
            print(f"   Unschedulable: {len(unschedulable_pods)}", flush=True)
            
            should_scale, reason = self.should_scale_up(metrics, unschedulable_pods)
            if should_scale:
                success = self.scale_up(reason)
                return success, "scale_up", reason
            
            should_scale, reason, node_name = self.should_scale_down(metrics)
            if should_scale:
                success = self.scale_down(reason, node_name)
                return success, "scale_down", reason
            
            print(f"   Status: {reason}", flush=True)
            return False, "no_action", reason
            
        except Exception as e:
            print(f"Error in node scaler check: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return False, "error", str(e)
    
    def get_status_summary(self):
        metrics = self.get_node_metrics()
        
        summary = {
            'current_nodes': metrics['node_count'],
            'min_nodes': self.min_nodes,
            'max_nodes': self.max_nodes,
            'avg_cpu_percent': metrics['avg_cpu_percent'],
            'avg_memory_percent': metrics['avg_memory_percent'],
            'total_pods': metrics['total_pods'],
            'last_action': self.last_scale_action,
            'last_action_time': self.last_scale_time.isoformat() if self.last_scale_time else None,
            'cooldown_remaining_seconds': self._cooldown_remaining(),
            'recent_history': [
                {
                    'timestamp': h['timestamp'].isoformat(),
                    'action': h['action'],
                    'nodes': f"{h['old_count']} → {h['new_count']}",
                    'reason': h['reason']
                }
                for h in self.scale_history[-10:]  # Last 10 actions
            ]
        }
        
        return summary
