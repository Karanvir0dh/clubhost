import asyncio
import csv
import io
import json
import os
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import CATALOG, page
from .content import Attendance, Registration, SaveContent, SiteContent, Visibility
from .provisioner import Kubernetes, Simulator, reconcile
from .store import Store

STATIC = Path(__file__).parent / 'static'
CLUBS = {'computing': 'Computing Society', 'outdoors': 'Outdoor Adventure Club'}
bearer = HTTPBearer(auto_error=False)


class NewApp(BaseModel):
    name: str = Field(min_length=3, max_length=48, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    template: Literal['club-site', 'event-page']
    version: Literal['v1', 'v2'] = 'v1'


class NewRelease(BaseModel):
    version: Literal['v1', 'v2']
    simulate_failure: bool = False


class Rollback(BaseModel):
    revision: int = Field(ge=1)


def create_app(data_dir=None, tokens=None, mode=None, start_worker=True):
    directory = Path(data_dir or os.getenv('CLUBHOST_DATA', '.data'))
    mode = mode or os.getenv('CLUBHOST_MODE', 'simulator')
    if mode not in {'simulator', 'kubernetes'}:
        raise RuntimeError('CLUBHOST_MODE must be simulator or kubernetes')
    context = os.getenv('CLUBHOST_KUBE_CONTEXT', '')
    store = Store(directory / 'clubhost.db', mode + ':' + context if mode == 'kubernetes' else mode)
    public_base_url = os.getenv('CLUBHOST_PUBLIC_BASE_URL', '').rstrip('/')
    SiteContent.valid_link(public_base_url)
    provisioner = Kubernetes(context, public_base_url) if mode == 'kubernetes' else Simulator()
    attempts = {}
    rate_lock = threading.Lock()

    def limit_registration(request):
        # Bound memory and count attempts, including duplicates, without storing attendee data.
        key = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        with rate_lock:
            for old_key in list(attempts):
                if not attempts[old_key] or attempts[old_key][-1] < now - 60:
                    del attempts[old_key]
            if key not in attempts and len(attempts) >= 10000:
                raise HTTPException(429, 'Please try again in a minute', headers={'Retry-After': '60'})
            queue = attempts.setdefault(key, deque())
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= 10:
                raise HTTPException(429, 'Too many requests. Please try again in a minute',
                                    headers={'Retry-After': '60'})
            queue.append(now)
    if tokens is None:
        token_path = directory / 'tokens.json'
        if not token_path.exists():
            token_path.write_text(json.dumps({club: secrets.token_urlsafe(32) for club in CLUBS}, indent=2))
        tokens = json.loads(token_path.read_text())
    if set(tokens) != set(CLUBS) or any(not isinstance(t, str) or len(t) < 24 for t in tokens.values()):
        raise RuntimeError('Provide a unique token of at least 24 characters for each seeded club')
    if len(set(tokens.values())) != len(tokens):
        raise RuntimeError('Club tokens must be distinct')

    async def work():
        while True:
            await asyncio.to_thread(reconcile, store, provisioner)
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(work()) if start_worker else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title='ClubHost', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.provisioner = provisioner

    def member(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials:
            for club, token in tokens.items():
                if secrets.compare_digest(credentials.credentials.encode(), token.encode()):
                    return club
        raise HTTPException(401, 'Enter a valid club access token', headers={'WWW-Authenticate': 'Bearer'})

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith(('/api', '/sites', '/public')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': mode}

    @app.get('/api/workspace')
    def workspace(club: Annotated[str, Depends(member)]):
        return {'club': {'id': club, 'name': CLUBS[club]}, 'mode': mode,
                'quota': 5, 'templates': list(CATALOG.values()), 'apps': store.list_apps(club)}

    @app.post('/api/apps', status_code=201)
    def create(body: NewApp, club: Annotated[str, Depends(member)]):
        return store.create(club, body.name, body.template, body.version)

    @app.get('/api/apps/{app_id}')
    def detail(app_id: str, club: Annotated[str, Depends(member)]):
        return store.detail(club, app_id)

    @app.post('/api/apps/{app_id}/releases', status_code=202)
    def release(app_id: str, body: NewRelease, club: Annotated[str, Depends(member)]):
        if body.simulate_failure and mode != 'simulator':
            raise HTTPException(422, 'Failure simulation is only available in simulator mode')
        return store.release(club, app_id, body.version, fail=body.simulate_failure)

    @app.post('/api/apps/{app_id}/rollback', status_code=202)
    def rollback(app_id: str, body: Rollback, club: Annotated[str, Depends(member)]):
        return store.release(club, app_id, rollback_to=body.revision)

    @app.post('/api/apps/{app_id}/content')
    def save_content(app_id: str, body: SaveContent, club: Annotated[str, Depends(member)]):
        return store.save_content(club, app_id, body.content.model_dump(), body.expected_version)

    @app.post('/api/apps/{app_id}/visibility')
    def visibility(app_id: str, body: Visibility, club: Annotated[str, Depends(member)]):
        return store.visibility(club, app_id, body.published)

    @app.get('/api/apps/{app_id}/attendees')
    def attendees(app_id: str, club: Annotated[str, Depends(member)]):
        return store.attendees(club, app_id)

    @app.post('/api/apps/{app_id}/attendees/{registration_id}')
    def attendance(app_id: str, registration_id: int, body: Attendance,
                   club: Annotated[str, Depends(member)]):
        return store.attendance(club, app_id, registration_id, body.action)

    @app.get('/api/apps/{app_id}/attendees.csv')
    def export_attendees(app_id: str, club: Annotated[str, Depends(member)]):
        rows = store.attendees(club, app_id)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['Name', 'Email', 'Status', 'Checked in', 'Registered (UTC)'])

        def safe_cell(value):
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

        for row in rows:
            writer.writerow([safe_cell(row['name']), safe_cell(row['email']), row['status'],
                             'Yes' if row['checked_in'] else 'No',
                             datetime.fromtimestamp(row['created'], UTC).isoformat()])
        return Response(output.getvalue(), media_type='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="attendees.csv"'})

    @app.get('/sites/{club}/{name}', response_class=HTMLResponse)
    def public_site(club: str, name: str):
        item = store.public_site(club, name)
        content = item['content']
        if content.get('event_date') and datetime.fromisoformat(
                content['event_date']) <= datetime.now(UTC):
            content = {**content, 'registration_open': False}
        return page(item['name'], item['template'], item['version'], content,
                    registration_path=f'/public/{club}/{name}/registrations')

    @app.post('/public/{club}/{name}/registrations', status_code=202)
    def register(club: str, name: str, body: Registration, request: Request):
        limit_registration(request)
        if not body.website:
            store.register(club, name, body.name, body.email)
        return {'message': 'Your request has been received. If you already registered, your existing request '
                           'is unchanged. Contact the organizer to confirm your place or waitlist status.'}

    @app.get('/api/apps/{app_id}/preview', response_class=HTMLResponse)
    def preview(app_id: str, club: Annotated[str, Depends(member)], draft: bool = False):
        item = store.detail(club, app_id)
        if draft:
            return page(item['name'], item['template'], item['releases'][0]['version'], item['draft'])
        active = next((r for r in item['releases'] if r['revision'] == item['active_revision']), None)
        if not active:
            raise HTTPException(409, 'No successful release is available')
        return page(item['name'], item['template'], active['version'], active['content'])

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(STATIC / 'index.html')

    return app
