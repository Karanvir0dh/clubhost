import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clubhost.main import create_app
from clubhost.provisioner import manifests, reconcile
from clubhost.store import Store

TOKENS = {'computing': 'computing-test-token-only-123456', 'outdoors': 'outdoors-test-token-only-123456'}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKENS, start_worker=False)) as client:
        client.headers['Authorization'] = 'Bearer ' + TOKENS['computing']
        yield client


def complete(client):
    with client.app.state.store.connect() as db:
        db.execute("UPDATE releases SET created=created-10 WHERE status='deploying'")
    reconcile(client.app.state.store, client.app.state.provisioner)


def event(client, capacity=2):
    app = client.post('/api/apps', json={'name': 'campus-hack-night', 'template': 'event-page'}).json()
    complete(client)
    content = {**app['draft'], 'title': 'Campus Hack Night', 'description': 'Bring a project and meet other students.',
               'location': 'Building B, Room 204', 'event_date': (datetime.now(UTC) + timedelta(days=7)).isoformat(),
               'registration_open': True, 'capacity': capacity}
    url = '/api/apps/' + app['id']
    assert client.post(url + '/content', json={'content': content, 'expected_version': 1}).status_code == 200
    assert client.post(url + '/releases', json={'version': 'v1'}).status_code == 202
    complete(client)
    assert client.post(url + '/visibility', json={'published': True}).status_code == 200
    return client.get(url).json(), url


def submit(client, name='Student One', email='one@example.com'):
    return client.post('/public/computing/campus-hack-night/registrations', json={'name': name, 'email': email})


def test_draft_release_snapshot_and_rollback(client):
    app, url = event(client)
    live = client.get(app['public_path'])
    assert 'Campus Hack Night' in live.text
    content = {**app['draft'], 'title': 'Secret Draft Title'}
    assert client.post(url + '/content', json={'content': content, 'expected_version': 2}).status_code == 200
    assert 'Secret Draft Title' not in client.get(app['public_path']).text
    assert 'Secret Draft Title' in client.get(url + '/preview?draft=true').text
    client.post(url + '/releases', json={'version': 'v2'})
    # Editing while a release is queued must not mutate that release's snapshot.
    client.post(url + '/content', json={'content': {**content, 'title': 'Later Draft'}, 'expected_version': 3})
    complete(client)
    assert 'Secret Draft Title' in client.get(app['public_path']).text
    assert 'Later Draft' not in client.get(app['public_path']).text
    client.post(url + '/rollback', json={'revision': 2})
    complete(client)
    assert 'Secret Draft Title' not in client.get(app['public_path']).text
    assert client.get(url).json()['draft']['title'] == 'Later Draft'


def test_private_pages_and_registration_closure(client):
    app, url = event(client)
    client.post(url + '/visibility', json={'published': False})
    assert client.get(app['public_path']).status_code == 404
    assert submit(client).status_code == 404
    client.post(url + '/visibility', json={'published': True})
    content = {**app['draft'], 'registration_open': False}
    client.post(url + '/content', json={'content': content, 'expected_version': 2})
    client.post(url + '/releases', json={'version': 'v1'})
    complete(client)
    assert submit(client).status_code == 409
    assert 'registration-form' not in client.get(app['public_path']).text


def test_duplicates_waitlist_promotion_checkin_and_csv(client):
    _, url = event(client, capacity=1)
    assert submit(client, '=SUM(1,2)', 'One@Example.com').status_code == 202
    assert submit(client, 'Duplicate Person', 'one@example.com').status_code == 202
    assert submit(client, 'Student Two', 'two@example.com').status_code == 202
    rows = client.get(url + '/attendees').json()
    assert [r['status'] for r in rows] == ['confirmed', 'waitlisted']
    assert client.post(url + f"/attendees/{rows[1]['id']}", json={'action': 'check-in'}).status_code == 409
    export = client.get(url + '/attendees.csv')
    assert "'=SUM(1,2)" in export.text
    assert export.headers['cache-control'] == 'no-store'
    client.post(url + f"/attendees/{rows[0]['id']}", json={'action': 'cancel'})
    updated = client.post(url + f"/attendees/{rows[1]['id']}", json={'action': 'check-in'}).json()
    assert updated[0]['status'] == 'cancelled'
    assert updated[1]['status'] == 'confirmed' and updated[1]['checked_in'] == 1
    client.post(url + f"/attendees/{rows[1]['id']}", json={'action': 'undo-check-in'})
    assert client.get(url + '/attendees').json()[1]['checked_in'] == 0


def test_parallel_registrations_never_overbook(client):
    app, url = event(client, capacity=3)
    store = client.app.state.store
    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(lambda i: store.register('computing', app['name'], f'Student {i}', f'{i}@example.com'),
                          range(24)))
    rows = client.get(url + '/attendees').json()
    assert len(rows) == 24
    assert sum(r['status'] == 'confirmed' for r in rows) == 3
    assert sum(r['status'] == 'waitlisted' for r in rows) == 21


def test_increased_capacity_promotes_oldest_waitlist_without_dropping_attendees(client):
    app, url = event(client, capacity=1)
    for i in range(3):
        submit(client, f'Student {i}', f'{i}@example.com')
    client.post(url + '/content', json={'content': {**app['draft'], 'capacity': 2}, 'expected_version': 2})
    client.post(url + '/releases', json={'version': 'v2'})
    complete(client)
    rows = client.get(url + '/attendees').json()
    assert [r['status'] for r in rows] == ['confirmed', 'confirmed', 'waitlisted']
    client.post(url + '/rollback', json={'revision': 2})
    complete(client)
    assert [r['status'] for r in client.get(url + '/attendees').json()] == ['confirmed', 'confirmed', 'waitlisted']


def test_authorization_for_content_visibility_and_attendee_data(client):
    app, url = event(client)
    submit(client)
    attendee = client.get(url + '/attendees').json()[0]
    client.headers['Authorization'] = 'Bearer ' + TOKENS['outdoors']
    assert client.post(url + '/content', json={'content': app['draft'], 'expected_version': 2}).status_code == 404
    assert client.post(url + '/visibility', json={'published': False}).status_code == 404
    for path in ['/attendees', '/attendees.csv', '/preview?draft=true']:
        assert client.get(url + path).status_code == 404
    assert client.post(url + f"/attendees/{attendee['id']}", json={'action': 'cancel'}).status_code == 404
    client.headers.clear()
    assert client.get(app['public_path']).status_code == 200
    assert 'one@example.com' not in client.get(app['public_path']).text
    assert client.get(url + '/attendees.csv').status_code == 401


def test_stale_draft_and_unsafe_content_validation(client):
    app, url = event(client)
    assert client.post(url + '/content', json={'content': app['draft'], 'expected_version': 1}).status_code == 409
    for changes in [{'link_url': 'javascript:alert(1)'}, {'event_date': '2027-03-01T10:00'},
                    {'capacity': 0}, {'registration_open': True, 'location': ''}, {'contact_email': 'broken'}]:
        assert client.post(url + '/content', json={'content': {**app['draft'], **changes},
                                                  'expected_version': 2}).status_code == 422
    content = {**app['draft'], 'title': '<script>alert(1)</script>'}
    client.post(url + '/content', json={'content': content, 'expected_version': 2})
    assert '<script>alert(1)</script>' not in client.get(url + '/preview?draft=true').text
    assert '&lt;script&gt;' in client.get(url + '/preview?draft=true').text


def test_rate_limit_honeypot_and_past_events(client):
    app, url = event(client)
    response = client.post('/public/computing/campus-hack-night/registrations',
                           json={'name': 'Spam Bot', 'email': 'bot@example.com', 'website': 'https://spam.test'})
    assert response.status_code == 202
    assert client.get(url + '/attendees').json() == []
    for _ in range(9):
        assert submit(client).status_code == 202
    assert submit(client).status_code == 429
    assert len(client.get(url + '/attendees').json()) == 1
    content = {**app['draft'], 'event_date': (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    client.post(url + '/content', json={'content': content, 'expected_version': 2})
    client.post(url + '/releases', json={'version': 'v1'})
    complete(client)
    assert 'registration-form' not in client.get(app['public_path']).text


def test_cluster_manifests_use_release_content(client):
    app, url = event(client)
    client.post(url + '/releases', json={'version': 'v2'})
    pending = client.app.state.store.pending()[0]
    html = manifests(pending, 'https://clubs.example.com')[0]['data']['index.html']
    assert 'Campus Hack Night' in html
    assert f'https://clubs.example.com{app["public_path"]}#register' in html


def test_original_database_migrates_without_losing_history(tmp_path):
    path = tmp_path / 'legacy.db'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE apps (id TEXT PRIMARY KEY,club TEXT,name TEXT,template TEXT,active_revision INTEGER,created REAL);
        CREATE TABLE releases (app_id TEXT,revision INTEGER,version TEXT,status TEXT,action TEXT,message TEXT,
            fail INTEGER DEFAULT 0,submitted INTEGER DEFAULT 0,created REAL,PRIMARY KEY(app_id,revision));
        INSERT INTO apps VALUES ('legacy','computing','original-site','club-site',1,100);
        INSERT INTO releases VALUES ('legacy',1,'v1','ready','deploy','original message',0,1,100);
    ''')
    db.close()
    store = Store(path, 'simulator')
    app = store.detail('computing', 'legacy')
    assert app['active_revision'] == 1 and app['published'] is False
    assert app['releases'][0]['message'] == 'original message'
    assert app['draft']['title'] == 'Original Site'
    store = Store(path, 'simulator')  # Repeating startup is safe.
    assert len(store.detail('computing', 'legacy')['releases']) == 1
    with store.connect() as db:
        assert json.loads(db.execute('SELECT content FROM releases').fetchone()[0]) == app['draft']
