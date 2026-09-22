import json
import math

import pytest

from careerops.store import Store, canonical_url, validate_settings
from careerops.tracker import ConflictError, application_write_lock


def role(**patch):
    return dict(title='Implementation Analyst', company='Example', url='https://example.org/jobs/1',
                description='Implementation Analyst. London. Python and client systems support.', **patch)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'tracker.sqlite3')


def test_bookmark_and_application_are_independent_and_durable(store):
    job = store.upsert_job(role())['job']
    job = store.action(job['id'], 'application', {'application': {'stage': 'applied', 'notes': 'Sent personally'}, 'expected_version': 0, 'confirmed_applied': True})
    application = job['application']
    for saved in [True, False, True]:
        job = store.action(job['id'], 'bookmark', {'bookmarked': saved})
        assert job['bookmarked'] is saved
        assert job['application'] == application
        assert job['status'] == 'applied'
    store.action(job['id'], 'materials_ready')
    store.action(job['id'], 'open')
    store.upsert_job(role(), trusted_history=True)
    store.rescore()
    reopened = Store(store.path).get_job(job['id'])
    assert reopened['application'] == application
    assert reopened['bookmarked'] is True
    assert reopened['status'] == 'applied'
    assert any(event['data'].get('actor') == 'user' and event['data'].get('before') for event in reopened['history'])


def test_confirmation_and_stale_version_are_atomic(store):
    job = store.upsert_job(role())['job']
    with pytest.raises(ValueError, match='Confirm'):
        store.action(job['id'], 'application', {'application': {'stage': 'applied'}, 'expected_version': 0})
    current = store.action(job['id'], 'application', {'application': {'stage': 'in_progress'}, 'expected_version': 0})
    history = current['history']
    with pytest.raises(ConflictError) as error:
        store.action(job['id'], 'application', {'application': {'notes': 'stale'}, 'expected_version': 0})
    assert error.value.current_application == current['application']
    assert store.get_job(job['id'])['history'] == history
    job = store.action(job['id'], 'mark_applied')
    stamp = job['application']['application_date']
    job = store.action(job['id'], 'application', {'application': {'notes': 'update'}, 'expected_version': job['application']['version']})
    assert job['application']['application_date'] == stamp
    assert job['application']['source'] == 'user_confirmed'


def test_every_stage_is_explicit_and_closure_is_not_rejection(store):
    job = store.upsert_job(role())['job']
    for stage in ['in_progress', 'applied', 'screening', 'interview', 'offer', 'rejected', 'withdrawn', 'not_started']:
        job = store.action(job['id'], 'application', {'application': {'stage': stage}, 'expected_version': job['application']['version'], 'confirmed_applied': True})
        assert job['application']['stage'] == stage
    job = store.action(job['id'], 'close')
    assert job['application']['stage'] == 'not_started'


def test_manual_empty_description_pending_then_verified_import_preserves_state(store):
    job = store.upsert_job({'title': 'Analyst', 'company': 'Example', 'url': 'https://example.org/manual', 'source': 'manual_review', 'last_verified': '2026-09-11'})['job']
    assert job['description'] == '' and job.get('last_verified') is None
    store.action(job['id'], 'bookmark', {'bookmarked': True})
    job = store.action(job['id'], 'application', {'application': {'stage': 'in_progress', 'next_action': 'Read criteria'}, 'expected_version': 0})
    app = job['application']
    updated = store.upsert_job(dict(job, description='London. Python systems implementation.', last_verified='2026-09-11T12:00:00+00:00'))['job']
    assert updated['application'] == app and updated['bookmarked']
    minimal = store.upsert_job({'title': 'Analyst', 'company': 'Example', 'url': job['url']})['job']
    assert minimal['description'] == updated['description']
    assert minimal['last_verified'] == updated['last_verified']


def test_import_cannot_forge_tracker_outcomes(store):
    job = store.upsert_job(role(application={'stage': 'applied'}, bookmarked=True, status='applied'))['job']
    assert job['application']['stage'] == 'not_started'
    assert not job['bookmarked']


def test_material_reference_requires_same_canonical_job(store):
    one = store.upsert_job(role())['job']
    two = store.upsert_job({'title': 'Other', 'company': 'Other', 'url': 'https://example.org/jobs/2', 'description': 'Python'})['job']
    with store.connect() as db:
        material = db.execute('INSERT INTO materials(job_id,cache_key,data) VALUES (?,?,?)', (two['id'], 'key', '{}')).lastrowid
    with pytest.raises(ValueError, match='belong'):
        store.action(one['id'], 'application', {'application': {'material_id': material}, 'expected_version': 0})


def test_query_job_ids_distinct_tracking_deduplicated_and_legacy_alias_migrates(store):
    base = {'title': 'Analyst', 'company': 'Example', 'description': 'Identical advertised job text.'}
    a = store.upsert_job(dict(base, url='https://example.org/job?ref=alpha&utm_source=x'))['job']
    b = store.upsert_job(dict(base, url='https://example.org/job?ref=beta'))['job']
    c = store.upsert_job(dict(base, url='https://example.org/job?id=123'))['job']
    assert len({a['id'], b['id'], c['id']}) == 3
    with store.connect() as db:
        db.execute('UPDATE jobs SET identity=? WHERE id=?', ('url:https://example.org/job', a['id']))
        db.execute("DELETE FROM metadata WHERE key='tracker_migration_version'")
    migrated = Store(store.path)
    duplicate = migrated.upsert_job(dict(base, url='https://example.org/job?ref=alpha&utm_source=y'))
    assert duplicate['duplicate'] and duplicate['job']['id'] == a['id']
    assert canonical_url('https://example.org/job?ref=1&source=abc&gh_src=2') == 'https://example.org/job?ref=1&source=abc'


def test_additive_migration_preserves_ids_and_history(store):
    job = store.upsert_job(role())['job']
    with store.connect() as db:
        data = dict(job, status='applied', manually_saved=True, applied_at='2026-08-21')
        for key in ('application', 'bookmarked', 'bookmarked_at', 'history', 'sources', 'materials'):
            data.pop(key, None)
        db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data), job['id']))
        db.execute("DELETE FROM metadata WHERE key='tracker_migration_version'")
        db.execute('PRAGMA user_version=2')
    migrated = Store(store.path).get_job(job['id'])
    assert migrated['id'] == job['id'] and migrated['bookmarked']
    assert migrated['application']['stage'] == 'applied'
    assert migrated['application']['application_date'] == '2026-08-21'
    assert migrated['history'] == job['history']
    assert list((store.path.parent / 'backups').glob('*.sqlite3'))


def test_cross_instance_lock_and_version_prevent_lost_write(store):
    job = store.upsert_job(role())['job']
    other = Store(store.path)
    with application_write_lock(store.path):
        with pytest.raises(ConflictError):
            other.action(job['id'], 'application', {'application': {'stage': 'in_progress'}, 'expected_version': 0})
    store.action(job['id'], 'application', {'application': {'stage': 'in_progress'}, 'expected_version': 0})
    with pytest.raises(ConflictError):
        other.action(job['id'], 'application', {'application': {'notes': 'old'}, 'expected_version': 0})


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True])
def test_strategy_weights_reject_invalid_numbers(value):
    with pytest.raises(ValueError):
        validate_settings({'strategy': {'weights': {'skills': value}}})


def test_professional_queue_rebuilds_from_full_candidates_without_old_pins(store, monkeypatch):
    first = store.upsert_job(role())['job']
    second = store.upsert_job({'title': 'Other', 'company': 'Two', 'url': 'https://example.org/jobs/2', 'description': 'Different London technical role.'})['job']
    store.action(first['id'], 'save')
    settings = store.settings()
    settings['strategy'] = {'mode': 'professional_london_first'}
    store.put_meta('settings', settings)
    calls = []
    def selector(jobs, settings):
        calls.append({j['id'] for j in jobs})
        return [j for j in jobs if j['id'] == second['id']]
    monkeypatch.setattr('careerops.store.select_shortlist', selector)
    selected = store.refresh_shortlist(replace=False)
    assert calls == [{first['id'], second['id']}]
    assert [j['id'] for j in selected] == [second['id']]
