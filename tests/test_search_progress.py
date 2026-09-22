"""A progress update must not reload the entire growing inventory."""
import threading

from careerops import discovery
from careerops.server import Application
from careerops.store import Store


def test_worker_uses_refreshed_shortlist_count(tmp_path, monkeypatch):
    app = Application(tmp_path / 'jobs.sqlite3')
    run = app.store.create_run('normal')
    run['scope'] = 'london'

    def discover(settings, mode, emit, cancelled):
        emit({'kind': 'job', 'job': {'title': 'Engineer', 'company': 'Example',
              'location': 'London', 'description': 'Build and test Python integrations.',
              'url': 'https://example.invalid/role'}})
        emit({'kind': 'checkpoint', 'checkpoint': {'pending': []}})
        return {'status': 'completed'}

    def no_inventory_reload():
        raise AssertionError('Progress must reuse the refresh result, not reload all jobs.')

    monkeypatch.setattr(discovery, 'discover', discover)
    monkeypatch.setattr(app.store, 'shortlist', no_inventory_reload)
    monkeypatch.setattr(app.store, 'refresh_shortlist', lambda **kwargs: [{}, {}])
    app._search_worker(run, threading.Event())
    saved = app.store.runs()[0]
    assert saved['found'] == 1 and saved['shortlisted'] == 2
    assert saved['status'] == 'completed'
    assert len(app.store.jobs()) == 1
    app.cv_execution.close()


def test_explicit_resume_renews_only_the_time_window(tmp_path):
    store = Store(tmp_path / 'jobs.sqlite3')
    checkpoint = {'elapsed_seconds': 180, 'spent_usd': '0.25',
                  'counts': {'pages': 3}, 'seen': ['https://example.invalid/one'],
                  'pending': [{'url': 'https://example.invalid/two'}]}
    prior = store.create_run('normal', checkpoint)
    store.update_run(prior['id'], {'status': 'time_limit', 'review_count': 2,
                                  'review_spend_usd': 0.1})
    resumed = store.create_run('normal', prior_id=prior['id'])
    assert resumed['checkpoint'] == {**checkpoint, 'elapsed_seconds': 0}
    assert resumed['review_count'] == 2 and resumed['review_spend_usd'] == 0.1
    saved_prior = next(run for run in store.runs() if run['id'] == prior['id'])
    assert saved_prior['checkpoint'] == checkpoint
    assert saved_prior['resumed_by'] == resumed['id']


def test_profile_readiness_uses_approved_evidence_rules(tmp_path):
    app = Application(tmp_path / 'jobs.sqlite3')
    try:
        assert not app.state()['evidence_ready']
        evidence = {'id': 'e1', 'text': 'Built Python reports.', 'status': 'verified'}
        app.store.update_profile({'evidence': [evidence]})
        assert app.state()['evidence_ready']
        app.store.update_profile({'evidence': [{**evidence, 'include_in_model': False}]})
        assert not app.state()['evidence_ready']
    finally:
        app.cv_execution.close()
