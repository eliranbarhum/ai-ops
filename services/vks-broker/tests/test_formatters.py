"""Unit tests for all formatter + parser functions in main.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import (
    _cpu_to_m, _mem_to_mib, _format_node, _format_pod,
    _format_workload, _format_event, _format_service,
    _format_pvc, _format_generic, _node_roles, _container_state,
)


# ── _cpu_to_m ────────────────────────────────────────────────────────────────

class TestCpuToM:
    def test_integer_cores(self):
        assert _cpu_to_m("2") == 2000.0

    def test_float_cores(self):
        assert _cpu_to_m("0.5") == 500.0

    def test_millicores(self):
        assert _cpu_to_m("250m") == 250.0

    def test_large_millicores(self):
        assert _cpu_to_m("4000m") == 4000.0

    def test_zero(self):
        assert _cpu_to_m("0") == 0.0

    def test_invalid_returns_zero(self):
        assert _cpu_to_m("bad") == 0.0

    def test_numeric_type_coerced(self):
        assert _cpu_to_m(4) == 4000.0


# ── _mem_to_mib ──────────────────────────────────────────────────────────────

class TestMemToMib:
    def test_kibibytes(self):
        assert _mem_to_mib("2048Ki") == 2

    def test_mibibytes(self):
        assert _mem_to_mib("512Mi") == 512

    def test_gibibytes(self):
        assert _mem_to_mib("4Gi") == 4096

    def test_tebibytes(self):
        assert _mem_to_mib("1Ti") == 1024 * 1024

    def test_raw_no_suffix_returned_as_is(self):
        # k8s always uses Ki/Mi/Gi suffixes; raw integer is returned unchanged
        assert _mem_to_mib("1048576") == 1048576

    def test_zero(self):
        assert _mem_to_mib("0") == 0

    def test_invalid_returns_zero(self):
        assert _mem_to_mib("bad") == 0


# ── _node_roles ───────────────────────────────────────────────────────────────

class TestNodeRoles:
    def test_control_plane(self):
        labels = {"node-role.kubernetes.io/control-plane": ""}
        assert "control-plane" in _node_roles(labels)

    def test_worker_fallback(self):
        assert _node_roles({}) == ["worker"]

    def test_multiple_roles(self):
        labels = {
            "node-role.kubernetes.io/control-plane": "",
            "node-role.kubernetes.io/etcd": "",
        }
        roles = _node_roles(labels)
        assert "control-plane" in roles
        assert "etcd" in roles

    def test_non_role_labels_ignored(self):
        labels = {"kubernetes.io/hostname": "node1", "beta.kubernetes.io/os": "linux"}
        assert _node_roles(labels) == ["worker"]


# ── _format_node ─────────────────────────────────────────────────────────────

def _make_node(name="node1", ready=True, unschedulable=False, cpu="4", mem="8Gi"):
    cond_status = "True" if ready else "False"
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "labels": {"node-role.kubernetes.io/control-plane": ""},
        },
        "spec": {"unschedulable": unschedulable, "taints": []},
        "status": {
            "conditions": [{"type": "Ready", "status": cond_status}],
            "allocatable": {"cpu": cpu, "memory": mem},
            "capacity": {"cpu": cpu, "memory": mem},
            "nodeInfo": {
                "kubeletVersion": "v1.32.0",
                "operatingSystem": "linux",
                "kernelVersion": "5.15.0",
                "containerRuntimeVersion": "containerd://1.7.0",
            },
        },
    }


class TestFormatNode:
    def test_ready_true(self):
        assert _format_node(_make_node(ready=True))["ready"] is True

    def test_ready_false(self):
        assert _format_node(_make_node(ready=False))["ready"] is False

    def test_unschedulable(self):
        assert _format_node(_make_node(unschedulable=True))["unschedulable"] is True

    def test_cpu_parsed(self):
        n = _format_node(_make_node(cpu="2"))
        assert n["allocatable_cpu_m"] == 2000.0

    def test_memory_parsed(self):
        n = _format_node(_make_node(mem="4Gi"))
        assert n["allocatable_mem_mib"] == 4096

    def test_roles_extracted(self):
        n = _format_node(_make_node())
        assert "control-plane" in n["roles"]

    def test_name_preserved(self):
        assert _format_node(_make_node(name="worker-1"))["name"] == "worker-1"


# ── _container_state ─────────────────────────────────────────────────────────

class TestContainerState:
    def test_running(self):
        assert _container_state({"state": {"running": {"startedAt": "2026-01-01"}}}) == "Running"

    def test_waiting_with_reason(self):
        assert _container_state({"state": {"waiting": {"reason": "CrashLoopBackOff"}}}) == "CrashLoopBackOff"

    def test_waiting_no_reason(self):
        assert _container_state({"state": {"waiting": {}}}) == "Waiting"

    def test_terminated(self):
        r = _container_state({"state": {"terminated": {"reason": "OOMKilled"}}})
        assert "OOMKilled" in r

    def test_unknown(self):
        assert _container_state({}) == "Unknown"


# ── _format_pod ───────────────────────────────────────────────────────────────

def _make_pod(name="pod-1", namespace="default", phase="Running", restarts=0):
    return {
        "metadata": {
            "name": name, "namespace": namespace,
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "ownerReferences": [{"kind": "Deployment", "name": "frontend"}],
        },
        "spec": {"nodeName": "worker-1"},
        "status": {
            "phase": phase,
            "podIP": "10.0.0.1",
            "hostIP": "192.168.1.1",
            "containerStatuses": [
                {"name": "app", "ready": True, "restartCount": restarts,
                 "image": "nginx:1.25", "state": {"running": {}}}
            ],
            "initContainerStatuses": [],
        },
    }


class TestFormatPod:
    def test_phase(self):
        assert _format_pod(_make_pod(phase="Pending"))["phase"] == "Pending"

    def test_restart_count(self):
        assert _format_pod(_make_pod(restarts=7))["restarts"] == 7

    def test_ready_string(self):
        assert _format_pod(_make_pod())["ready"] == "1/1"

    def test_owner_extracted(self):
        assert _format_pod(_make_pod())["owner"] == "Deployment/frontend"

    def test_containers_list(self):
        pod = _format_pod(_make_pod())
        assert len(pod["containers"]) == 1
        assert pod["containers"][0]["name"] == "app"

    def test_node_name(self):
        assert _format_pod(_make_pod())["node"] == "worker-1"

    def test_namespace(self):
        assert _format_pod(_make_pod(namespace="kube-system"))["namespace"] == "kube-system"

    def test_crashloop_false_healthy(self):
        assert _format_pod(_make_pod(restarts=0))["crashloop"] is False

    def test_crashloop_true_high_restarts(self):
        assert _format_pod(_make_pod(restarts=5))["crashloop"] is True

    def test_crashloop_true_on_crashloopbackoff(self):
        pod = _make_pod()
        pod["status"]["containerStatuses"][0]["state"] = {"waiting": {"reason": "CrashLoopBackOff"}}
        pod["status"]["containerStatuses"][0]["restartCount"] = 1
        assert _format_pod(pod)["crashloop"] is True

    def test_crashloop_false_low_restarts(self):
        assert _format_pod(_make_pod(restarts=2))["crashloop"] is False


# ── _format_service ──────────────────────────────────────────────────────────

def _make_service(name="svc", svc_type="ClusterIP", port=80):
    return {
        "metadata": {"name": name, "namespace": "default",
                     "creationTimestamp": "2026-01-01T00:00:00Z", "labels": {}},
        "spec": {
            "type": svc_type,
            "clusterIP": "10.96.0.1",
            "ports": [{"port": port, "protocol": "TCP", "targetPort": port}],
            "selector": {"app": name},
        },
    }


class TestFormatService:
    def test_type(self):
        assert _format_service(_make_service(svc_type="LoadBalancer"))["type"] == "LoadBalancer"

    def test_cluster_ip(self):
        assert _format_service(_make_service())["cluster_ip"] == "10.96.0.1"

    def test_ports(self):
        svc = _format_service(_make_service(port=8080))
        assert any(p.get("port") == 8080 for p in svc["ports"])


# ── _format_event ─────────────────────────────────────────────────────────────

def _make_event(kind="Warning", reason="BackOff", msg="Back-off restarting"):
    return {
        "metadata": {"name": "evt-1", "namespace": "default"},
        "type": kind,
        "reason": reason,
        "message": msg,
        "involvedObject": {"kind": "Pod", "name": "pod-1", "namespace": "default"},
        "lastTimestamp": "2026-06-12T00:00:00Z",
        "count": 3,
    }


class TestFormatEvent:
    def test_type(self):
        assert _format_event(_make_event(kind="Warning"))["type"] == "Warning"

    def test_message(self):
        assert _format_event(_make_event(msg="hello"))["message"] == "hello"

    def test_reason(self):
        assert _format_event(_make_event(reason="Pulled"))["reason"] == "Pulled"

    def test_object_string(self):
        evt = _format_event(_make_event())
        assert "Pod" in evt["object"] or "pod" in evt["object"].lower()


# ── _format_pvc ───────────────────────────────────────────────────────────────

class TestFormatPvc:
    def test_basic(self):
        pvc = {
            "metadata": {"name": "data", "namespace": "default",
                         "creationTimestamp": "2026-01-01T00:00:00Z"},
            "spec": {"storageClassName": "standard", "accessModes": ["ReadWriteOnce"],
                     "resources": {"requests": {"storage": "10Gi"}},
                     "volumeName": "pvc-abc"},
            "status": {"phase": "Bound", "capacity": {"storage": "10Gi"}},
        }
        result = _format_pvc(pvc)
        assert result["name"] == "data"
        assert result["status"] == "Bound"   # _format_pvc uses "status" key for phase
        assert result["storage_class"] == "standard"
