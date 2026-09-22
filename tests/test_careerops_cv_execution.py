"""Controller tests use synthetic evidence and explicitly mocked model calls."""
from copy import deepcopy
import json
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest
from careerops import cv_execution as engine
from careerops import model_connections as connections


class FakeStore:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()
        self.values = {'profile': {'name': 'Candidate Example', 'evidence': [{'id': 'e1', 'text': 'Built a test system.', 'status': 'verified'}]},
                       'settings': {'model_connections': {**{r: {'provider': 'codex_cli' if r != 'blue' else 'claude_cli', 'model': 'test-model', 'effort': 'high'} for r in connections.ROLES}, 'timeout_seconds': 30, 'max_concurrent_runs': 1}}}
        self.jobs = {1: {'id': 1, 'title': 'Synthetic Analyst', 'description': 'Explain systems.', 'status': 'new'}, 2: {'id': 2, 'title': 'Other job'}}
        self.materials = {}
        self.keys = {}
        with self.connect() as db:
            db.execute('CREATE TABLE cv_runs(id INTEGER PRIMARY KEY,job_id INTEGER,idempotency_key TEXT UNIQUE,data TEXT)')

    def connect(self):
        value = sqlite3.connect(self.path)
        value.row_factory = sqlite3.Row
        return value

    def get_job(self, i): return deepcopy(self.jobs[i])
    def profile(self): return deepcopy(self.values['profile'])
    def settings(self): return deepcopy(self.values['settings'])
    def meta(self, key, fallback=None): return deepcopy(self.values.get(key, fallback))
    def put_meta(self, key, value, version=False): self.values[key] = deepcopy(value)
    def material(self, i): return deepcopy(self.materials[i])
    def save_material(self, job_id, key, data):
        if key not in self.keys:
            i = len(self.materials) + 1
            self.keys[key] = i
            self.materials[i] = dict(deepcopy(data), id=i, job_id=job_id)
        return self.material(self.keys[key])


@pytest.fixture
def system(tmp_path, monkeypatch):
    store = FakeStore(tmp_path / 'test.sqlite3')
    calls = []
    base = {'id': 3, 'sha256': 'base-hash', 'filename': 'synthetic.docx', 'text': 'Synthetic CV'}
    monkeypatch.setattr(engine.CVExecution, '_base', lambda self: deepcopy(base))
    monkeypatch.setattr(connections, 'status', lambda store: {'ready': True, 'setup_blockers': [], 'configuration': store.settings()['model_connections']})
    def execute(connection, payload, schema, role, **kwargs):
        calls.append((role, deepcopy(payload)))
        response = ({'summary': 'Mocked synthesis', 'decisions': [{'finding_id': f['id'], 'decision': 'agree', 'reason': 'Supported by the frozen review.'} for f in payload['findings']], 'letter_paragraphs': [{'evidence_ids': ['e1']}]} if role == 'purple' else {'findings': [], 'summary': 'Mocked independent review'})
        return {'response': response, 'provider': connection['provider'],
                'actual_model': 'test-model', 'mocked': True, 'usage': {'input_tokens': 1}, 'tool_calls': 0}
    monkeypatch.setattr(connections, 'execute', execute)
    def build(job, profile, proposal, direction=None, base_cv=None):
        return {'cv_text': 'Synthetic grounded CV', 'sections': [], 'formats': ['docx', 'pdf'], 'direction': direction, 'blocked_proposals': []}
    helpers = SimpleNamespace(
        generator_schema=lambda: {}, review_schema=lambda: {},
        generation_packet=lambda job, profile, base, direction: {'job': job, 'evidence': profile['evidence'], 'direction': direction},
        build_document=build, validate_document=lambda *args: {'status': 'passed'},
        review_packet=lambda job, profile, material, direction: {'advert': job['description'], 'evidence': profile['evidence'], 'cv': material['cv_text'], 'rubric': 'frozen-v1', 'direction': direction},
        validate_review=lambda response, job, profile, material, team: dict(response, team=team),
        apply_findings=lambda material, findings, profile, job, accepted_ids=None: (deepcopy(material), []))
    monkeypatch.setitem(sys.modules, 'careerops.cv_document', helpers)
    import careerops
    monkeypatch.setattr(careerops, 'cv_document', helpers, raising=False)
    monkeypatch.setattr(engine.CVExecution, '_check_exports', lambda *args: None)
    runner = engine.CVExecution(store, autostart=False)
    return runner, store, calls, helpers, base


def test_double_click_frozen_inputs_independent_reviews_and_no_apply(system):
    runner, store, calls, helpers, base = system
    first = runner.start(1, {'idempotency_key': 'one'})
    again = runner.start(1, {'idempotency_key': 'two'})
    assert first['id'] == again['id']
    store.values['profile']['evidence'][0]['text'] = 'Changed afterwards'
    store.jobs[1]['description'] = 'Changed advert'
    base['sha256'] = 'replaced'
    runner._run(first['id'])
    run = runner.get(first['id'])
    assert run['status'] == 'ready'
    assert [role for role, _ in calls] == ['generator', 'red', 'blue', 'purple']
    assert calls[1][1] == calls[2][1]
    assert calls[1][1]['advert'] == 'Explain systems.'
    assert calls[1][1]['evidence'][0]['text'] == 'Built a test system.'
    assert run['base_cv_version']['sha256'] == 'base-hash'
    assert store.jobs[1]['status'] == 'new'
    assert 'snapshot' not in run
    assert all('response' not in r for r in run['receipts'].values())
    assert store.meta('cv_model_live_receipts') is None


def test_uncertain_retry_needs_consent_reuses_completed_calls(system, monkeypatch):
    runner, store, calls, helpers, _ = system
    original = connections.execute
    fail = {'blue': True}
    def execute(connection, payload, schema, role, **kwargs):
        if role == 'blue' and fail['blue']:
            calls.append((role, payload))
            raise connections.ConnectionError('Timed out after dispatch.', uncertain=True)
        return original(connection, payload, schema, role, **kwargs)
    monkeypatch.setattr(connections, 'execute', execute)
    run = runner.start(1)
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'uncertain'
    with pytest.raises(ValueError, match='Explicitly confirm'):
        runner.retry(run['id'])
    fail['blue'] = False
    runner.retry(run['id'], explicit_uncertain=True)
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'ready'
    assert [role for role, _ in calls] == ['generator', 'red', 'blue', 'blue', 'purple']
    assert len(store.materials) == 1


def test_partial_review_restart_uses_frozen_packet_and_schema_and_clears_error(system, monkeypatch):
    runner, store, calls, helpers, _ = system
    original = connections.execute
    schemas = []
    fail = {'blue': True}
    helpers.review_schema = lambda: {'rubric_schema': 'v2'}
    def execute(connection, payload, schema, role, **kwargs):
        if role in ('red', 'blue'):
            schemas.append(deepcopy(schema))
            private = runner._read(run['id'])
            assert private['review_inputs']['a:r0']['packet'] == payload
        if role == 'blue' and fail['blue']:
            calls.append((role, deepcopy(payload)))
            raise connections.ConnectionError('Synthetic failed reviewer.')
        return original(connection, payload, schema, role, **kwargs)
    monkeypatch.setattr(connections, 'execute', execute)
    run = runner.start(1)
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'failed'
    original_packet = deepcopy(calls[1][1])
    assert calls[2][1] == original_packet
    assert 'review_inputs' not in runner.get(run['id'])

    def changed_builder(*args):
        raise AssertionError('Retry must not rebuild reviewer input or schema.')
    helpers.review_packet = helpers.review_schema = changed_builder
    replacement = engine.CVExecution(store, autostart=False)
    fail['blue'] = False
    replacement.retry(run['id'])
    replacement._run(run['id'])
    public = replacement.get(run['id'])
    assert public['status'] == 'ready'
    assert [role for role, _ in calls] == ['generator', 'red', 'blue', 'blue', 'purple']
    assert calls[-2][1] == original_packet
    assert schemas == [{'rubric_schema': 'v2'}] * 3
    assert 'review_inputs' not in public
    assert 'error' not in public['receipts']['a:r0:blue']
    private_receipt = replacement._read(run['id'])['receipts']['a:r0:blue']
    assert private_receipt['previous_attempts'][0]['error'] == 'Synthetic failed reviewer.'


@pytest.mark.parametrize('missing_hash', [False, True])
def test_historical_completed_review_rejects_changed_or_unknown_input_without_calls(system, monkeypatch, missing_hash):
    runner, _, calls, helpers, _ = system
    original = connections.execute
    def execute(connection, payload, schema, role, **kwargs):
        if role == 'blue':
            calls.append((role, deepcopy(payload)))
            raise connections.ConnectionError('Synthetic failed reviewer.')
        return original(connection, payload, schema, role, **kwargs)
    monkeypatch.setattr(connections, 'execute', execute)
    run = runner.start(1)
    runner._run(run['id'])
    private = runner._read(run['id'])
    private.pop('review_inputs')  # A historical run predating durable packets.
    if missing_hash:
        private['receipts']['a:r0:red'].pop('packet_hash')
    else:
        old_builder = helpers.review_packet
        helpers.review_packet = lambda *args: dict(old_builder(*args), rubric='changed-v3')
    runner._write(private)
    runner.retry(run['id'])
    runner._run(run['id'])
    result = runner.get(run['id'])
    assert result['status'] == 'failed'
    assert 'new Review Again' in result['error']
    assert [role for role, _ in calls] == ['generator', 'red', 'blue']


def test_comparison_review_also_reuses_frozen_input_after_partial_failure(system, monkeypatch):
    runner, store, calls, helpers, _ = system
    original = connections.execute
    fail = {'blue': True}
    def execute(connection, payload, schema, role, **kwargs):
        if 'comparison' not in payload:
            return original(connection, payload, schema, role, **kwargs)
        calls.append((role, deepcopy(payload)))
        if role == 'blue' and fail['blue']:
            raise connections.ConnectionError('Synthetic comparison failure.')
        return {'response': {'material_id': payload['comparison'][0]['material_id'], 'reasons': ['Synthetic preference']},
                'provider': connection['provider'], 'actual_model': 'test-model', 'mocked': True}
    monkeypatch.setattr(connections, 'execute', execute)
    run = runner.start(1, {'action': 'compare'})
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'failed'
    old_packet = deepcopy(calls[-2][1])
    assert calls[-1][1] == old_packet
    call_count = len(calls)
    helpers.review_packet = lambda *args: {'rubric': 'new-v3-must-not-be-used'}
    fail['blue'] = False
    replacement = engine.CVExecution(store, autostart=False)
    replacement.retry(run['id'])
    replacement._run(run['id'])
    assert replacement.get(run['id'])['status'] == 'ready'
    assert len(calls) == call_count + 1
    assert calls[-1] == ('blue', old_packet)


def test_restart_preserves_receipts_and_marks_ambiguous_dispatch(system):
    runner, store, _, _, _ = system
    run = runner.start(1)
    private = runner._read(run['id'])
    private.update(status='running', receipts={'a:generation': {'status': 'completed', 'response': {'old': True}},
                                             'a:r0:red': {'status': 'dispatching'}})
    runner._write(private)
    replacement = engine.CVExecution(store, autostart=False)
    replacement._recover()
    recovered = replacement._read(run['id'])
    assert recovered['status'] == 'uncertain'
    assert recovered['requires_explicit_retry'] is True
    assert recovered['receipts']['a:generation']['response'] == {'old': True}
    assert recovered['receipts']['a:r0:red']['status'] == 'uncertain'


def test_document_failure_retry_does_not_repeat_models(system, monkeypatch):
    runner, _, calls, _, _ = system
    def failed_export(*args): raise ValueError('Synthetic renderer failure')
    monkeypatch.setattr(runner, '_check_exports', failed_export)
    run = runner.start(1)
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'failed'
    monkeypatch.setattr(runner, '_check_exports', lambda *args: None)
    runner.retry(run['id'])
    runner._run(run['id'])
    assert runner.get(run['id'])['status'] == 'ready'
    assert len(calls) == 4


def test_reviews_bounded_to_two_revisions_and_findings_have_run_ids(system):
    runner, _, calls, helpers, _ = system
    original = helpers.validate_review
    helpers.validate_review = lambda response, job, profile, material, team: {'team': team, 'summary': '', 'findings': [{
        'category': 'document_problem', 'cv_passage': 'Synthetic', 'evidence_ids': ['e1'],
        'severity': 'low', 'recommended_action': 'Bring evidence earlier.', 'action': {}, 'validation': {'status': 'eligible'}}]}
    def improve(material, findings, profile, job, accepted_ids=None):
        return dict(material, cv_text=material['cv_text'] + ' improved'), [{'finding_id': findings[0]['id'], 'status': 'accepted', 'reason': 'Moved supported evidence earlier.'}]
    helpers.apply_findings = improve
    run = runner.start(1)
    runner._run(run['id'])
    result = runner.get(run['id'])
    assert result['status'] == 'ready'
    assert len(result['material_ids']) == 3
    assert len(calls) == 8
    assert all(f['id'].startswith(str(run['id']) + ':') for f in result['suggestions'])
    assert result['what_improved'] == ['Bring evidence earlier.']
    assert result['suggestions'][0]['status'] == 'applied'


def test_cancel_queued_run_makes_no_calls_and_selection_can_undo(system):
    runner, store, calls, _, _ = system
    run = runner.start(1)
    assert runner.cancel(run['id'])['status'] == 'cancelled'
    runner._run(run['id'])
    assert calls == []
    one = store.save_material(1, 'one', {'cv_text': 'A'})
    two = store.save_material(1, 'two', {'cv_text': 'B'})
    runner.select(1, one['id'])
    changed = runner.select(1, two['id'])
    runner.select(1, changed['previous_material_id'])
    assert store.meta('cv_selected:1') == one['id']
    assert store.jobs[1]['status'] == 'new'
    with pytest.raises(ValueError, match='another job'):
        runner.select(2, one['id'])


def test_review_again_skips_generator_and_missing_connection_stays_setup(system, monkeypatch):
    runner, store, calls, _, _ = system
    material = store.save_material(1, 'existing', {'cv_text': 'Prior CV', 'direction': {}, 'blocked_proposals': []})
    run = runner.start(1, {'action': 'review_again', 'material_id': material['id']})
    runner._run(run['id'])
    assert [role for role, _ in calls] == ['red', 'blue', 'purple']
    monkeypatch.setattr(connections, 'status', lambda store: {'ready': False, 'setup_blockers': ['Connect reviewer'], 'configuration': store.settings()['model_connections']})
    blocked = runner.start(1, {'idempotency_key': 'missing'})
    assert blocked['status'] == 'needs_setup'
    assert 'Connect reviewer' in blocked['needs_attention']


def test_real_thread_keeps_running_without_browser_and_reuses_owner(system):
    runner, store, calls, _, _ = system
    active = engine.CVExecution(store)
    try:
        same = engine.CVExecution(store)
        assert same._owner is active
        run = same.start(1)
        # Bounded wait on an actual worker; no simulated UI timer.
        import time
        deadline = time.monotonic() + 3
        while active.get(run['id'])['status'] in engine.ACTIVE and time.monotonic() < deadline:
            time.sleep(.02)
        assert active.get(run['id'])['status'] == 'ready'
        assert len(calls) == 4
    finally:
        active.close()


def test_mutable_job_workflow_fields_do_not_start_duplicate_run(system):
    runner, store, _, _, _ = system
    one = runner.start(1, {'idempotency_key': 'before'})
    store.jobs[1].update(status='materials_ready', bookmarked=True, application_opened_at='2026-09-11')
    two = runner.start(1, {'idempotency_key': 'after'})
    assert one['id'] == two['id']


def test_public_receipts_do_not_leak_previous_attempt_responses(system):
    runner, _, _, _, _ = system
    one = runner.start(1)
    private = runner._read(one['id'])
    private['receipts']['call'] = {'status': 'failed', 'response': {'private': 'current'},
                                  'previous_attempts': [{'response': {'private': 'earlier'}}]}
    runner._write(private)
    assert 'private' not in json.dumps(runner.get(one['id']))


def test_unmentioned_factual_conflicts_and_eligibility_gaps_stay_visible(system):
    runner, _, _, helpers, _ = system
    original = helpers.build_document
    def build(*args, **kwargs):
        value = original(*args, **kwargs)
        value.update(base_cv_conflicts=[{'field': 'qualification', 'message': 'Degree title differs.'}],
                     analysis={'evidence_match': {'gaps': ['Work permission needs checking.']}},
                     requires_human_review=True)
        return value
    helpers.build_document = build
    one = runner.start(1)
    runner._run(one['id'])
    result = runner.get(one['id'])
    assert result['status'] == 'needs_answer'
    assert 'Work permission needs checking.' in result['needs_attention']
    assert 'Base CV conflict: Degree title differs.' in result['needs_attention']
