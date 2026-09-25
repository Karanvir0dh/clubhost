import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from .catalog import CATALOG
from .content import default_content


class Store:
    def __init__(self, path: Path, mode: str):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS apps (
                    id TEXT PRIMARY KEY, club TEXT NOT NULL, name TEXT NOT NULL,
                    template TEXT NOT NULL, active_revision INTEGER,
                    created REAL NOT NULL, UNIQUE(club,name)
                );
                CREATE TABLE IF NOT EXISTS releases (
                    app_id TEXT NOT NULL REFERENCES apps(id), revision INTEGER NOT NULL,
                    version TEXT NOT NULL, status TEXT NOT NULL, action TEXT NOT NULL,
                    message TEXT NOT NULL, fail INTEGER NOT NULL DEFAULT 0,
                    submitted INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                    PRIMARY KEY(app_id,revision)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_app
                    ON releases(app_id) WHERE status='deploying';
            ''')
            prior = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            if prior and prior['value'] != mode:
                raise RuntimeError("Use a separate CLUBHOST_DATA directory for each infrastructure mode/context.")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)", (mode,))
        self.migrate()

    def migrate(self):
        # Preserve existing apps and release history when adding content and events.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(apps)')}
            for name, definition in [('draft', "TEXT NOT NULL DEFAULT '{}'"),
                                     ('draft_version', 'INTEGER NOT NULL DEFAULT 1'),
                                     ('published', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE apps ADD COLUMN {name} {definition}')
            if 'content' not in {r['name'] for r in db.execute('PRAGMA table_info(releases)')}:
                db.execute("ALTER TABLE releases ADD COLUMN content TEXT NOT NULL DEFAULT '{}'")
            for app in db.execute("SELECT id,name FROM apps WHERE draft='{}'").fetchall():
                content = json.dumps(default_content(app['name']))
                db.execute('UPDATE apps SET draft=? WHERE id=?', (content, app['id']))
                db.execute("UPDATE releases SET content=? WHERE app_id=? AND content='{}'", (content, app['id']))
            db.execute('''CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL REFERENCES apps(id),
                name TEXT NOT NULL, email TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('confirmed','waitlisted','cancelled')),
                checked_in INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                UNIQUE(app_id,email)
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS registrations_queue ON registrations(app_id,status,id)')
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('schema_version','2')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def owned(db, club, app_id):
        row = db.execute("SELECT * FROM apps WHERE id=? AND club=?", (app_id, club)).fetchone()
        if not row:
            raise HTTPException(404, "Application not found")
        return dict(row)

    def list_apps(self, club):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM apps WHERE club=? ORDER BY created DESC", (club,)).fetchall()
            return [self._detail(db, club, row['id']) for row in rows]

    def _detail(self, db, club, app_id):
        app = self.owned(db, club, app_id)
        app['releases'] = [dict(row) for row in db.execute(
            "SELECT * FROM releases WHERE app_id=? ORDER BY revision DESC", (app_id,))]
        app['draft'] = json.loads(app['draft'])
        app['published'] = bool(app['published'])
        app['public_path'] = f"/sites/{app['club']}/{app['name']}"
        for release in app['releases']:
            release['content'] = json.loads(release['content'])
        return app

    def detail(self, club, app_id):
        with self.connect() as db:
            return self._detail(db, club, app_id)

    @staticmethod
    def validate(template, version):
        if template not in CATALOG or version not in CATALOG[template]['versions']:
            raise HTTPException(422, "Choose an approved template and version")

    def create(self, club, name, template, version):
        self.validate(template, version)
        app_id = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM apps WHERE club=?", (club,)).fetchone()[0]
            if count >= 5:
                raise HTTPException(409, "This club has reached its five-application quota")
            try:
                db.execute("INSERT INTO apps (id,club,name,template,created,draft) VALUES (?,?,?,?,?,?)",
                           (app_id, club, name, template, time.time(), json.dumps(default_content(name))))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "An application with that name already exists") from None
            self._insert_release(db, app_id, 1, version, 'deploy', False, default_content(name))
        return self.detail(club, app_id)

    @staticmethod
    def _insert_release(db, app_id, revision, version, action, fail, content):
        db.execute("INSERT INTO releases (app_id,revision,version,status,action,message,fail,created,content) "
                   "VALUES (?,?,?,'deploying',?,?,?,?,?)",
                   (app_id, revision, version, action, 'Queued for provisioning', int(fail),
                    time.time(), json.dumps(content)))

    def release(self, club, app_id, version=None, rollback_to=None, fail=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            app = self.owned(db, club, app_id)
            releases = db.execute("SELECT * FROM releases WHERE app_id=? ORDER BY revision DESC",
                                  (app_id,)).fetchall()
            if any(row['status'] == 'deploying' for row in releases):
                raise HTTPException(409, "Wait for the current release to finish")
            action = 'deploy'
            content = json.loads(app['draft'])
            if rollback_to is not None:
                target = next((r for r in releases if r['revision'] == rollback_to and r['status'] == 'ready'), None)
                if not target:
                    raise HTTPException(422, "Rollback requires a previously successful revision")
                version, action, fail = target['version'], f'rollback to r{rollback_to}', False
                content = json.loads(target['content'])
            self.validate(app['template'], version)
            self._insert_release(db, app_id, releases[0]['revision'] + 1, version, action, fail, content)
        return self.detail(club, app_id)

    def pending(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
                SELECT r.*, a.club, a.name, a.template FROM releases r JOIN apps a ON a.id=r.app_id
                WHERE r.status='deploying' ORDER BY r.created
            ''')]

    def submitted(self, release):
        with self.connect() as db:
            db.execute("UPDATE releases SET submitted=1,message='Waiting for readiness checks' "
                       "WHERE app_id=? AND revision=? AND status='deploying'",
                       (release['app_id'], release['revision']))

    def finish(self, release, status, message):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE releases SET status=?,message=? "
                                "WHERE app_id=? AND revision=? AND status='deploying'",
                                (status, message, release['app_id'], release['revision']))
            if status == 'ready' and result.rowcount:
                db.execute("UPDATE apps SET active_revision=? WHERE id=?",
                           (release['revision'], release['app_id']))
                self._promote(db, release['app_id'])

    def save_content(self, club, app_id, content, expected_version):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            app = self.owned(db, club, app_id)
            if app['draft_version'] != expected_version:
                raise HTTPException(409, 'The draft changed in another session. Reopen the editor to load it.')
            if app['template'] != 'event-page' and content['registration_open']:
                raise HTTPException(422, 'Registration is only available for event pages')
            db.execute('UPDATE apps SET draft=?,draft_version=draft_version+1 WHERE id=?',
                       (json.dumps(content), app_id))
        return self.detail(club, app_id)

    def visibility(self, club, app_id, published):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            app = self.owned(db, club, app_id)
            if published and app['active_revision'] is None:
                raise HTTPException(409, 'Deploy a successful release before enabling the public page')
            db.execute('UPDATE apps SET published=? WHERE id=?', (int(published), app_id))
        return self.detail(club, app_id)

    @staticmethod
    def _live(db, club, name):
        row = db.execute('''SELECT a.*,r.content,r.version FROM apps a JOIN releases r
            ON r.app_id=a.id AND r.revision=a.active_revision
            WHERE a.club=? AND a.name=? AND a.published=1 AND r.status='ready' ''', (club, name)).fetchone()
        if not row:
            raise HTTPException(404, 'Published page not found')
        app = dict(row)
        app['content'] = json.loads(app['content'])
        return app

    def public_site(self, club, name):
        with self.connect() as db:
            return self._live(db, club, name)

    def register(self, club, name, attendee_name, email):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            app = self._live(db, club, name)
            content = app['content']
            if (app['template'] != 'event-page' or not content.get('registration_open')
                    or not content.get('event_date')
                    or datetime.fromisoformat(content['event_date']) <= datetime.now(UTC)):
                raise HTTPException(409, 'Registration is closed for this event')
            prior = db.execute('SELECT id FROM registrations WHERE app_id=? AND email=?',
                               (app['id'], email)).fetchone()
            if prior:
                # Duplicate submissions do not disclose prior attendance or reactivate cancellations.
                return
            count = db.execute("SELECT count(*) FROM registrations WHERE app_id=? AND status='confirmed'",
                               (app['id'],)).fetchone()[0]
            status = 'confirmed' if count < content['capacity'] else 'waitlisted'
            db.execute('INSERT INTO registrations (app_id,name,email,status,created) VALUES (?,?,?,?,?)',
                       (app['id'], attendee_name, email, status, time.time()))

    @staticmethod
    def _promote(db, app_id):
        row = db.execute('''SELECT r.content FROM apps a JOIN releases r
            ON r.app_id=a.id AND r.revision=a.active_revision WHERE a.id=?''', (app_id,)).fetchone()
        if not row:
            return
        content = json.loads(row['content'])
        if not content.get('event_date') or datetime.fromisoformat(
                content['event_date']) <= datetime.now(UTC):
            return
        count = db.execute("SELECT count(*) FROM registrations WHERE app_id=? AND status='confirmed'",
                           (app_id,)).fetchone()[0]
        places = max(0, content['capacity'] - count)
        db.execute("UPDATE registrations SET status='confirmed' WHERE id IN ("
                   "SELECT id FROM registrations WHERE app_id=? AND status='waitlisted' ORDER BY id LIMIT ?)",
                   (app_id, places))

    def attendees(self, club, app_id):
        with self.connect() as db:
            self.owned(db, club, app_id)
            return [dict(row) for row in db.execute('SELECT * FROM registrations WHERE app_id=? ORDER BY id',
                                                    (app_id,))]

    def attendance(self, club, app_id, registration_id, action):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.owned(db, club, app_id)
            row = db.execute('SELECT * FROM registrations WHERE id=? AND app_id=?',
                             (registration_id, app_id)).fetchone()
            if not row:
                raise HTTPException(404, 'Registration not found')
            if action == 'cancel':
                db.execute("UPDATE registrations SET status='cancelled',checked_in=0 WHERE id=?", (registration_id,))
                self._promote(db, app_id)
            else:
                if row['status'] != 'confirmed':
                    raise HTTPException(409, 'Only confirmed attendees can be checked in')
                db.execute('UPDATE registrations SET checked_in=? WHERE id=?',
                           (int(action == 'check-in'), registration_id))
        return self.attendees(club, app_id)
