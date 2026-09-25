"""Exercise the real browser flow against an isolated temporary database.

Install optional requirements-browser.lock first. Uses installed Microsoft Edge;
set CLUBHOST_BROWSER_CHANNEL=chromium to use a Playwright-installed Chromium.
"""
import os
import socket
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from clubhost.main import create_app


def main():
    data_root = Path('.data').resolve()
    data_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='browser-test-', dir=data_root) as directory:
        assert Path(directory).resolve().is_relative_to(data_root)
        tokens = {'computing': 'browser-computing-token-123456789', 'outdoors': 'browser-outdoors-token-123456789'}
        app = create_app(directory, tokens, mode='simulator')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.1)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=os.getenv('CLUBHOST_BROWSER_CHANNEL', 'msedge'),
                                                     headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{port}'
                page.goto(base)
                page.get_by_label('Club access token').fill(tokens['computing'])
                page.get_by_role('button', name='Open workspace').click()
                page.get_by_role('button', name='Use template').nth(1).click()
                page.get_by_label('Application name', exact=True).fill('browser-workshop')
                page.get_by_role('button', name='Create and deploy').click()
                page.get_by_role('button', name='Edit content', exact=True).click()
                page.get_by_label('Page title', exact=True).fill('Campus Web Workshop')
                page.get_by_label('Tagline', exact=True).fill('Build and ship your first website.')
                page.get_by_label('About your club or event').fill('Bring a laptop. Beginners welcome.')
                page.get_by_label('Event date and time', exact=False).fill(
                    (datetime.now(UTC).astimezone() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'))
                page.get_by_label('Location or meeting instructions').fill('Library, room 204')
                page.get_by_label('Available places').fill('1')
                page.get_by_label('Accept registrations').check()
                page.get_by_role('button', name='Save draft', exact=True).click()
                expect(page.get_by_role('button', name='Deploy saved draft')).to_be_enabled(timeout=10000)
                page.get_by_role('button', name='Deploy saved draft').click()
                expect(page.get_by_text('Draft matches the live content', exact=True)).to_be_visible(timeout=10000)
                page.get_by_role('button', name='Enable public page', exact=True).click()
                public_link = page.get_by_role('link', name='Open public page')
                expect(public_link).to_be_visible()
                visitor = context.new_page()
                visitor.on('pageerror', lambda error: errors.append(str(error)))
                visitor.goto(base + public_link.get_attribute('href'))
                expect(visitor.get_by_role('heading', name='Campus Web Workshop')).to_be_visible()
                for name, email in [('Student One', 'one@example.com'), ('Student Two', 'two@example.com')]:
                    visitor.get_by_label('Your name').fill(name)
                    visitor.get_by_label('Email address').fill(email)
                    visitor.get_by_role('button', name='Request a place').click()
                    expect(visitor.get_by_label('Your name')).to_have_value('')
                    expect(visitor.get_by_role('status')).to_contain_text('Your request has been received')
                page.get_by_role('button', name='Manage attendees').click()
                expect(page.get_by_role('heading', name='1 confirmed · 1 waiting')).to_be_visible()
                page.get_by_role('button', name='Check in', exact=True).click()
                expect(page.get_by_text('confirmed · Checked in', exact=True)).to_be_visible()
                with page.expect_download() as download:
                    page.get_by_role('button', name='Download CSV').click()
                csv = Path(download.value.path()).read_text()
                assert 'one@example.com' in csv and 'waitlisted' in csv
                page.on('dialog', lambda dialog: dialog.accept())
                page.locator('.attendee').filter(has_text='Student One').get_by_role('button', name='Cancel place').click()
                expect(page.get_by_role('heading', name='1 confirmed · 0 waiting')).to_be_visible()
                page.locator('#attendees-dialog .close-dialog').click()
                page.screenshot(path=str(data_root / 'product-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(data_root / 'product-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
                print('Browser flow passed: edit, publish, register, waitlist, check-in, CSV, cancellation, mobile layout.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            assert not thread.is_alive(), 'Test server did not stop'


if __name__ == '__main__':
    main()
