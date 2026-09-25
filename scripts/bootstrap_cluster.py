"""Print resources for review. This script does not contact or modify a cluster."""
import json

items = []
for club in ('computing', 'outdoors'):
    namespace = 'club-' + club
    metadata = {'name': namespace, 'namespace': namespace}
    items.extend([
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {
            'name': namespace, 'labels': {'pod-security.kubernetes.io/enforce': 'restricted'}}},
        {'apiVersion': 'v1', 'kind': 'ResourceQuota', 'metadata': metadata,
         'spec': {'hard': {'requests.cpu': '1', 'requests.memory': '1Gi', 'limits.cpu': '2',
                           'limits.memory': '2Gi', 'pods': '12', 'services': '5', 'configmaps': '100'}}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
         'metadata': {**metadata, 'name': 'default-deny'},
         'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress']}},
    ])
print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}, indent=2))
