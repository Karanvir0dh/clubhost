import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from clubhost.main import create_app
from clubhost.provisioner import Kubernetes, manifests, reconcile
from clubhost.store import Store

TOKENS = {'computing': 'computing-test-token-only-123456', 'outdoors': 'outdoors-test-token-only-123456'}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, TOKENS, start_worker=False)
    with TestClient(app) as test_client:
        test_client.headers['Authorization'] = 'Bearer ' + TOKENS['computing']
        yield test_client


def create(client, name='coding-night'):
    response = client.post('/api/apps', json={'name': name, 'template': 'club-site'})
    assert response.status_code == 201
    return response.json()


def complete(client):
    with client.app.state.store.connect() as db:
        db.execute("UPDATE releases SET created=created-10 WHERE status='deploying'")
    reconcile(client.app.state.store, client.app.state.provisioner)


def test_failed_release_then_rollback_keeps_history(client):
    app = create(client)
    url = '/api/apps/' + app['id']
    complete(client)
    assert client.get(url).json()['active_revision'] == 1
    assert client.post(url + '/releases', json={'version': 'v2', 'simulate_failure': True}).status_code == 202
    complete(client)
    failed = client.get(url).json()
    assert failed['active_revision'] == 1
    assert failed['releases'][0]['status'] == 'failed'
    assert client.post(url + '/rollback', json={'revision': 2}).status_code == 422
    assert client.post(url + '/rollback', json={'revision': 1}).status_code == 202
    complete(client)
    restored = client.get(url).json()
    assert restored['active_revision'] == 3
    assert [r['status'] for r in restored['releases']] == ['ready', 'failed', 'ready']
    assert restored['releases'][0]['version'] == 'v1'
    assert 'v1' in client.get(url + '/preview').text


def test_tenant_isolation_on_every_app_endpoint(client):
    app = create(client)
    complete(client)
    url = '/api/apps/' + app['id']
    client.headers['Authorization'] = 'Bearer ' + TOKENS['outdoors']
    assert client.get('/api/workspace').json()['apps'] == []
    assert client.get(url).status_code == 404
    assert client.get(url + '/preview').status_code == 404
    assert client.post(url + '/releases', json={'version': 'v2'}).status_code == 404
    assert client.post(url + '/rollback', json={'revision': 1}).status_code == 404


def test_authentication_and_server_side_catalog(client):
    client.headers.clear()
    assert client.get('/api/workspace').status_code == 401
    client.headers['Authorization'] = 'Bearer invalid'
    assert client.post('/api/apps', json={'name': 'abc', 'template': 'club-site'}).status_code == 401
    client.headers['Authorization'] = 'Bearer ' + TOKENS['computing']
    for body in [{'name': '../bad', 'template': 'club-site'},
                 {'name': 'valid-name', 'template': 'arbitrary-image'},
                 {'name': 'valid-name', 'template': 'club-site', 'version': 'v999'}]:
        assert client.post('/api/apps', json=body).status_code == 422


def test_quota_and_duplicate_names(client):
    create(client, 'club-one')
    assert client.post('/api/apps', json={'name': 'club-one', 'template': 'club-site'}).status_code == 409
    for i in range(4):
        create(client, f'club-{i}')
    assert client.post('/api/apps', json={'name': 'club-six', 'template': 'event-page'}).status_code == 409


def test_parallel_release_requests_only_accept_one(client):
    app = create(client)
    complete(client)
    store = client.app.state.store

    def request_release(_):
        try:
            store.release('computing', app['id'], 'v2')
            return 202
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(request_release, range(8)))
    assert results.count(202) == 1
    assert results.count(409) == 7
    assert len(store.detail('computing', app['id'])['releases']) == 2


def test_restart_recovers_queued_release(client):
    app = create(client)
    store = client.app.state.store
    with store.connect() as db:
        db.execute('UPDATE releases SET created=created-10')
    restarted = Store(store.path, 'simulator')
    reconcile(restarted, client.app.state.provisioner)
    assert restarted.detail('computing', app['id'])['active_revision'] == 1
    with pytest.raises(RuntimeError):
        Store(store.path, 'kubernetes:other-context')


def test_transient_infrastructure_error_retries(client):
    app = create(client)

    class Unavailable:
        def apply(self, release):
            raise RuntimeError('unavailable')

    store = client.app.state.store
    reconcile(store, Unavailable())
    assert store.detail('computing', app['id'])['releases'][0]['status'] == 'deploying'
    with store.connect() as db:
        db.execute('UPDATE releases SET created=created-200')
    reconcile(store, Unavailable())
    result = store.detail('computing', app['id'])
    assert result['releases'][0]['status'] == 'failed'
    assert result['active_revision'] is None


def test_kubernetes_manifest_boundaries_and_observed_generation(client, monkeypatch):
    create(client)
    release = client.app.state.store.pending()[0]
    resources = manifests(release)
    assert all(r['metadata']['namespace'] == 'club-computing' for r in resources)
    deployment = resources[1]
    pod = deployment['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['containers'][0]['securityContext']['allowPrivilegeEscalation'] is False
    assert deployment['spec']['strategy']['rollingUpdate']['maxUnavailable'] == 0
    cluster = Kubernetes('test-context')
    import json
    deployment['metadata']['generation'] = 2
    deployment['status'] = {'observedGeneration': 1, 'updatedReplicas': 1, 'availableReplicas': 1, 'replicas': 1}
    monkeypatch.setattr(cluster, 'run', lambda *args: json.dumps(deployment))
    assert cluster.observe(release) is None
    deployment['status']['observedGeneration'] = 2
    assert cluster.observe(release)[0] == 'ready'
    deployment['status']['replicas'] = 2
    assert cluster.observe(release) is None
    deployment['status']['conditions'] = [{'reason': 'ProgressDeadlineExceeded'}]
    assert cluster.observe(release)[0] == 'failed'


def test_dashboard_assets_and_openapi(client):
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/static/style.css').status_code == 200
    schema = client.get('/openapi.json').json()
    assert schema['paths']['/api/workspace']['get']['security'] == [{'HTTPBearer': []}]


def test_lifespan_worker_completes_release_without_manual_reconcile(tmp_path):
    with TestClient(create_app(tmp_path, TOKENS)) as client:
        client.headers['Authorization'] = 'Bearer ' + TOKENS['computing']
        app = create(client)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = client.get('/api/apps/' + app['id']).json()
            if result['active_revision'] == 1:
                break
            time.sleep(0.25)
        assert result['active_revision'] == 1
        assert client.get('/api/apps/' + app['id'] + '/preview').status_code == 200
