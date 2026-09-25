import json
import subprocess
import time

from .catalog import page


class Simulator:
    def apply(self, release):
        pass

    def observe(self, release):
        if time.time() - release['created'] < 4:
            return None
        if release['fail']:
            return 'failed', 'Simulated readiness failure. The last successful release is retained.'
        return 'ready', 'Simulated readiness checks passed. No cluster resources were created.'


def manifests(release, public_base_url=''):
    name = 'app-' + release['app_id']
    namespace = 'club-' + release['club']
    config_name = f"{name}-r{release['revision']}"
    labels = {'app': name, 'app.kubernetes.io/managed-by': 'clubhost'}
    metadata = {'name': name, 'namespace': namespace, 'labels': labels}
    return [
        {'apiVersion': 'v1', 'kind': 'ConfigMap',
         'metadata': {**metadata, 'name': config_name},
         'data': {'index.html': page(release['name'], release['template'], release['version'],
                                    json.loads(release.get('content', '{}')),
                                    portal_url=f"{public_base_url}/sites/{release['club']}/{release['name']}"
                                    if public_base_url else '')}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata,
         'spec': {'replicas': 1, 'revisionHistoryLimit': 3, 'progressDeadlineSeconds': 120,
                  'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxUnavailable': 0, 'maxSurge': 1}},
                  'selector': {'matchLabels': {'app': name}},
                  'template': {'metadata': {'labels': labels,
                                            'annotations': {'clubhost/revision': str(release['revision'])}},
                               'spec': {'automountServiceAccountToken': False,
                                        'securityContext': {'runAsNonRoot': True, 'runAsUser': 101,
                                                            'runAsGroup': 101, 'fsGroup': 101,
                                                            'seccompProfile': {'type': 'RuntimeDefault'}},
                                        'containers': [{'name': 'web',
                                            'image': 'nginxinc/nginx-unprivileged:1.28-alpine',
                                            'ports': [{'containerPort': 8080}],
                                            'securityContext': {'allowPrivilegeEscalation': False,
                                                                'readOnlyRootFilesystem': True,
                                                                'capabilities': {'drop': ['ALL']}},
                                            'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'},
                                                          'limits': {'cpu': '200m', 'memory': '128Mi'}},
                                            'readinessProbe': {'httpGet': {'path': '/', 'port': 8080},
                                                               'initialDelaySeconds': 2, 'periodSeconds': 3},
                                            'volumeMounts': [{'name': 'content', 'mountPath': '/usr/share/nginx/html',
                                                              'readOnly': True},
                                                             {'name': 'tmp', 'mountPath': '/tmp'}]}],
                                        'volumes': [{'name': 'content', 'configMap': {'name': config_name}},
                                                    {'name': 'tmp', 'emptyDir': {}}]}}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata,
         'spec': {'selector': {'app': name}, 'ports': [{'port': 80, 'targetPort': 8080}]}},
    ]


class Kubernetes:
    def __init__(self, context, public_base_url=''):
        if not context:
            raise RuntimeError('CLUBHOST_KUBE_CONTEXT must explicitly identify a development cluster')
        self.context = context
        self.public_base_url = public_base_url

    def run(self, args, payload=None):
        result = subprocess.run(['kubectl', '--context', self.context, *args],
                                input=payload, capture_output=True, text=True, timeout=20, check=False)
        if result.returncode:
            # Keep raw cluster errors out of tenant-facing responses.
            raise RuntimeError('Kubernetes request failed; check context, namespace, permissions, and cluster connectivity')
        return result.stdout

    def apply(self, release):
        self.run(['apply', '-f', '-'], json.dumps({'apiVersion': 'v1', 'kind': 'List',
                                                 'items': manifests(release, self.public_base_url)}))

    def observe(self, release):
        obj = json.loads(self.run(['-n', 'club-' + release['club'], 'get', 'deployment',
                                   'app-' + release['app_id'], '-o', 'json']))
        status = obj.get('status', {})
        observed = status.get('observedGeneration', 0) >= obj['metadata']['generation']
        revision = obj['spec']['template']['metadata']['annotations'].get('clubhost/revision')
        if revision != str(release['revision']):
            return 'failed', 'Deployment changed outside this release; inspect the cluster before retrying.'
        if observed:
            if any(c.get('reason') == 'ProgressDeadlineExceeded' for c in status.get('conditions', [])):
                return 'failed', 'Readiness deadline exceeded. Roll back to a successful revision.'
            desired = obj['spec'].get('replicas', 1)
            if (status.get('updatedReplicas', 0) == desired
                    and status.get('availableReplicas', 0) == desired
                    and status.get('replicas', 0) == desired):
                return 'ready', 'Kubernetes reports the requested revision is available.'
        if time.time() - release['created'] > 180:
            return 'failed', 'Rollout timed out. Inspect cluster events and retry or roll back.'
        return None


def reconcile(store, provisioner):
    for release in store.pending():
        try:
            if not release['submitted']:
                provisioner.apply(release)
                store.submitted(release)
            result = provisioner.observe(release)
            if result:
                store.finish(release, *result)
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            if time.time() - release['created'] > 180:
                store.finish(release, 'failed', 'Infrastructure could not be confirmed within 180 seconds. '
                             'Resources may exist; inspect the cluster before retrying.')
