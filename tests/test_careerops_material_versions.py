"""Real SQLite coverage for shared CV history allocation; no model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

from careerops.cv_execution import CVExecution
from careerops.store import Store


def test_old_version_revision_two_approaches_and_retry_keep_global_history(tmp_path):
    store = Store(tmp_path / 'versions.sqlite3')
    job = store.upsert_job({'title': 'Synthetic analyst', 'company': 'Example',
                           'url': 'https://example.org/jobs/version-test',
                           'description': 'Python systems analysis in London.'})['job']
    job_id = job['id']
    originals = [store.save_material(job_id, f'original:{n}', {'cv_text': f'CV {n}', 'version': 1})
                 for n in range(6)]
    old = originals[2]
    run = {'id': 1, 'job_id': job_id, 'snapshot': {'source_material': old},
           'snapshot_hash': 'frozen', 'base_cv_version': {}, 'evidence_version': 'evidence',
           'advert_version': 'advert', 'material_ids': []}
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS cv_runs(id INTEGER PRIMARY KEY,job_id INTEGER,idempotency_key TEXT UNIQUE,data TEXT)')
        db.execute('INSERT INTO cv_runs(id,job_id,idempotency_key,data) VALUES (?,?,?,?)', (1, job_id, 'mocked-version-run', json.dumps(run)))
    runner = CVExecution(store, autostart=False)
    branches = []
    for branch in ('a', 'b'):
        parent = old['id']
        for stage in range(3):
            material = runner._save_material(run, branch, stage, old, parent=parent)
            branches.append(material)
            parent = material['id']
    assert [m['version'] for m in originals + branches] == list(range(1, 13))
    assert branches[0]['parent_id'] == branches[3]['parent_id'] == old['id']
    assert store.material(old['id']) == old

    # Manual editing shares the allocator, even when its source carries v3.
    manual = store.save_material(job_id, 'manual-edit', dict(old, cv_text='Manual CV', version=4))
    assert manual['version'] == 13
    replay_run = runner._read(1)
    for index, material in enumerate(branches):
        replay = runner._save_material(replay_run, 'a' if index < 3 else 'b', index % 3,
                                       {'cv_text': 'must not replace saved output'}, parent=old['id'])
        assert replay == material
    assert runner._read(1)['material_ids'] == [m['id'] for m in branches]
    assert len(store.get_job(job_id)['materials']) == 13
    assert store.save_material(job_id + 1, 'different-job', {'cv_text': 'Other'})['version'] == 1


def test_legacy_versions_preserved_and_input_not_mutated(tmp_path):
    store = Store(tmp_path / 'legacy.sqlite3')
    with store.connect() as db:
        for key, data in [('old-one', {'version': 8}), ('old-two', {'version': 8}), ('unversioned', {})]:
            db.execute('INSERT INTO materials(job_id,cache_key,data) VALUES (?,?,?)', (1, key, json.dumps(data)))
    value = {'version': 2, 'cv_text': 'New revision'}
    before = deepcopy(value)
    assert store.save_material(1, 'new', value)['version'] == 9
    assert value == before
    assert store.save_material(1, 'old-one', value)['version'] == 8
    assert 'version' not in store.save_material(1, 'unversioned', value)


def test_separate_store_instances_allocate_atomically_and_replay_once(tmp_path):
    first = Store(tmp_path / 'concurrent.sqlite3')
    second = Store(first.path)
    barrier = threading.Barrier(2)
    def save(store, key):
        barrier.wait(timeout=5)
        return store.save_material(1, key, {'cv_text': 'Synthetic CV', 'version': 1})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(save, first, 'a')
        b = pool.submit(save, second, 'b')
        assert sorted([a.result()['version'], b.result()['version']]) == [1, 2]
        a = pool.submit(save, first, 'same-stage')
        b = pool.submit(save, second, 'same-stage')
        assert a.result() == b.result()
        assert a.result()['version'] == 3
