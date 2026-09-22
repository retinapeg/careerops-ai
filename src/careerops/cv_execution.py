"""Persistent, bounded CV generation and independent review. Never applies to jobs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import threading

from careerops import model_connections

DIRECTIONS = [{'value': key, 'label': label} for key, label in (
    ('balanced', 'Balanced for this job'), ('applied_ai', 'Applied AI / implementation'),
    ('client_solutions', 'Client-facing solutions'), ('technical_depth', 'Technical depth / engineering'),
    ('quantitative', 'Quantitative problem-solving'), ('concise', 'More concise'))]
ACTIVE = {'queued', 'running'}
DEFAULT_POSITIONING = 'Applied AI Engineer | AI Solutions & Implementation'
_REGISTRY, _REGISTRY_LOCK = {}, threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _public(run):
    if not run:
        return None
    value = deepcopy(run)
    value.pop('snapshot', None)
    value.pop('review_inputs', None)
    value['emphasis'] = value.get('direction', {}).get('emphasis')
    value['instruction'] = value.get('direction', {}).get('instruction', '')
    safe_receipt_keys = {'status', 'role', 'provider', 'model', 'requested_model', 'actual_model',
        'model_identity_source', 'cli_version', 'auth_method', 'tool_calls', 'usage', 'started_at',
        'completed_at', 'elapsed_seconds', 'packet_hash', 'error', 'mocked'}
    value['receipts'] = {key: {field: data for field, data in receipt.items() if field in safe_receipt_keys}
                         for key, receipt in value.get('receipts', {}).items()}
    return value


def _direction(data):
    emphasis = data.get('emphasis', 'balanced')
    if emphasis == 'technical':
        emphasis = 'technical_depth'
    if emphasis not in {item['value'] for item in DIRECTIONS}:
        raise ValueError('Choose one of the supported CV directions.')
    instruction = data.get('instruction', '')
    if not isinstance(instruction, str) or len(instruction) > 1000:
        raise ValueError('Use a short direction instruction of at most 1,000 characters.')
    return {'emphasis': emphasis, 'instruction': instruction.strip(), 'positioning': DEFAULT_POSITIONING}


class CVExecution:
    """One worker per private DB, with stage receipts committed before continuing.

    The app owns table creation. Refreshing a web page does not affect this
    worker. A process restart resumes safe stages, but never repeats an uncertain
    provider dispatch without an explicit retry request.
    """
    def __init__(self, store, *, autostart=True):
        self.store = store
        self._key = str(Path(store.path).resolve())
        self._wake = threading.Event()
        self._cancel_events = {}
        self._closed = False
        self._owner = self
        if autostart:
            with _REGISTRY_LOCK:
                owner = _REGISTRY.get(self._key)
                if owner and not owner._closed:
                    self._owner = owner
                    return
                _REGISTRY[self._key] = self
            self._recover()
            self._thread = threading.Thread(target=self._work, name='careerops-cv', daemon=True)
            self._thread.start()
            self._wake.set()

    def _read(self, run_id):
        with self.store.connect() as db:
            row = db.execute('SELECT id,job_id,data FROM cv_runs WHERE id=?', (int(run_id),)).fetchone()
        if not row:
            raise KeyError('CV run not found.')
        return dict(json.loads(row['data']), id=row['id'], job_id=row['job_id'])

    def _write(self, run):
        run['updated_at'] = _now()
        with self.store.lock, self.store.connect() as db:
            db.execute('UPDATE cv_runs SET data=? WHERE id=?', (json.dumps(run, ensure_ascii=False), run['id']))
        return run

    def get(self, run_id):
        return _public(self._read(run_id))

    def list(self, job_id):
        with self.store.connect() as db:
            rows = db.execute('SELECT id FROM cv_runs WHERE job_id=? ORDER BY id DESC', (int(job_id),)).fetchall()
        return [self.get(row['id']) for row in rows]

    def _all(self):
        with self.store.connect() as db:
            return [dict(json.loads(row['data']), id=row['id'], job_id=row['job_id'])
                    for row in db.execute('SELECT * FROM cv_runs ORDER BY id')]

    def _base(self):
        from careerops import base_cv
        return base_cv.selected(self.store)

    def workspace(self, job_id):
        self.store.get_job(job_id)
        runs = self.list(job_id)
        connections = model_connections.status(self.store)
        base = self._base()
        blockers = list(connections['setup_blockers'])
        if not base:
            blockers.insert(0, 'Upload your base CV once in Your CV.')
        return {'job_id': int(job_id), 'runs': runs,
                'active_run': next((run for run in runs if run['status'] in ACTIVE), None),
                'latest_run': runs[0] if runs else None,
                'selected_material_id': self.store.meta(f'cv_selected:{job_id}'),
                'base_cv_ready': bool(base), 'connections_ready': connections['ready'],
                'setup_blockers': blockers, 'directions': DIRECTIONS,
                'reviewer_mode': 'automatic', 'max_automatic_rounds': 2}

    def start(self, job_id, data=None):
        if self._owner is not self:
            return self._owner.start(job_id, data)
        data = data or {}
        if not isinstance(data, dict):
            raise ValueError('CV run options must be an object.')
        action = data.get('action', data.get('mode', 'start'))
        if action not in {'start', 'revise', 'compare', 'review_again'}:
            raise ValueError('Unsupported CV workflow action.')
        current_job = self.store.get_job(job_id)
        advert_fields = ('id', 'title', 'company', 'description', 'requirements', 'location', 'locations',
            'country', 'url', 'canonical_url', 'source_type', 'source_id', 'salary', 'posted_at',
            'work_pattern', 'seniority', 'job_type', 'evaluation')
        job = deepcopy({key: current_job[key] for key in advert_fields if key in current_job})
        profile, base = deepcopy(self.store.profile()), deepcopy(self._base())
        direction = _direction(data)
        material_id = data.get('material_id')
        material = None
        if material_id is not None:
            material = self.store.material(int(material_id))
            if material['job_id'] != int(job_id):
                raise ValueError('The selected CV belongs to another job.')
        if material and action in {'review_again', 'revise'}:
            inherited = material.get('direction', {})
            direction = _direction(dict(inherited, **{key: data[key] for key in ('emphasis', 'instruction') if key in data}))
        if material and action in {'review_again', 'revise'} and material.get('cv_run_id'):
            original = self._read(material['cv_run_id'])
            if original['job_id'] != int(job_id):
                raise ValueError('The selected CV has an invalid frozen source.')
            frozen = original['snapshot']
            job, profile, base = deepcopy(frozen['job']), deepcopy(frozen['profile']), deepcopy(frozen['base_cv'])
        if action in {'review_again', 'revise'} and not material:
            raise ValueError('Choose a generated CV to revise or review again.')
        selected, rejected = data.get('selected_findings', []), data.get('rejected_findings', [])
        if any(not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) for v in value) for value in (selected, rejected)):
            raise ValueError('Selected changes must be a short list of finding identifiers.')
        available = {f['id']: f for previous in reversed(self.list(job_id)) for f in previous.get('suggestions', [])}
        if any(value not in available for value in selected + rejected):
            raise ValueError('A selected suggestion is no longer available for this job.')
        if material and any(available[value].get('material_id') != material['id'] for value in selected):
            raise ValueError('Select suggestions for the CV version being revised.')
        connections = model_connections.status(self.store)
        snapshot = {'job': job, 'profile': profile, 'base_cv': base, 'direction': direction,
                    'connections': connections['configuration'], 'source_material': material,
                    'selected_findings': [available[value] for value in selected],
                    'rejected_findings': rejected, 'rubric_version': 'coherent-cv-v1'}
        intent = _hash({'snapshot': snapshot, 'action': action})
        idempotency = data.get('idempotency_key') or intent
        if not isinstance(idempotency, str) or not 1 <= len(idempotency) <= 200:
            raise ValueError('Invalid CV run request identifier.')
        blockers = list(connections['setup_blockers'])
        if not base:
            blockers.insert(0, 'Upload your base CV once in Your CV.')
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT id,job_id FROM cv_runs WHERE idempotency_key=?', (idempotency,)).fetchone()
            if previous:
                if previous['job_id'] != int(job_id):
                    raise ValueError('This request identifier belongs to another job.')
                return self.get(previous['id'])
            # A second click may use a fresh browser UUID: deduplicate the same
            # intent while active as well as requests sharing an idempotency key.
            for row in db.execute('SELECT id,data FROM cv_runs WHERE job_id=?', (int(job_id),)):
                prior = json.loads(row['data'])
                if prior.get('status') in ACTIVE and prior.get('intent') == intent:
                    return self.get(row['id'])
            run = {'job_id': int(job_id), 'action': action, 'intent': intent, 'snapshot_hash': _hash(snapshot),
                   'snapshot': snapshot, 'direction': direction, 'created_at': _now(), 'updated_at': _now(),
                   'status': 'needs_setup' if blockers else 'queued', 'stage': 'setup' if blockers else 'queued',
                   'label': 'Connect reviewer' if blockers else 'Preparing draft', 'receipts': {},
                   'material_ids': [], 'selected_material_id': None, 'suggestions': [], 'reviews': [],
                   'what_improved': [], 'needs_attention': blockers, 'comparison': None,
                   'error': None, 'requires_explicit_retry': False, 'cancel_requested': False,
                   'base_cv_version': {'id': base.get('id'), 'sha256': base.get('sha256'), 'filename': base.get('filename')} if base else None,
                   'evidence_version': _hash(profile), 'advert_version': _hash(job),
                   'selected_findings': selected, 'rejected_findings': rejected}
            run['id'] = db.execute('INSERT INTO cv_runs(job_id,idempotency_key,data) VALUES (?,?,?)',
                                  (int(job_id), idempotency, json.dumps(run))).lastrowid
        self._wake.set()
        return _public(run)

    def cancel(self, run_id):
        if self._owner is not self:
            return self._owner.cancel(run_id)
        with self.store.lock:
            run = self._read(run_id)
            if run['status'] not in ACTIVE:
                return _public(run)
            run['cancel_requested'] = True
            event = self._cancel_events.get(int(run_id))
            if event:
                event.set()
            else:
                run.update(status='cancelled', stage='cancelled', label='Cancelled')
            self._write(run)
        return _public(run)

    def retry(self, run_id, explicit_uncertain=False):
        if self._owner is not self:
            return self._owner.retry(run_id, explicit_uncertain)
        with self.store.lock:
            run = self._read(run_id)
            if run['status'] in ACTIVE or run['status'] in {'ready', 'needs_answer'}:
                return _public(run)
            if run['requires_explicit_retry'] and explicit_uncertain is not True:
                raise ValueError('This model call may already have completed. Explicitly confirm a retry before repeating it.')
            if run['status'] == 'needs_setup':
                connections, base = model_connections.status(self.store), self._base()
                if not base or not connections['ready']:
                    raise ValueError('Upload your base CV and connect all three model roles before retrying.')
                run['snapshot']['base_cv'] = deepcopy(base)
                run['snapshot']['connections'] = connections['configuration']
                run['base_cv_version'] = {key: base.get(key) for key in ('id', 'sha256', 'filename')}
                run['snapshot_hash'] = _hash(run['snapshot'])
            for receipt in run['receipts'].values():
                if receipt['status'] in {'uncertain', 'dispatching', 'failed'}:
                    receipt.setdefault('previous_attempts', []).append({key: value for key, value in receipt.items() if key != 'previous_attempts'})
                    receipt['status'] = 'pending'
            run.update(status='queued', stage='queued', label='Preparing draft', error=None,
                       requires_explicit_retry=False, cancel_requested=False, needs_attention=[])
            self._write(run)
        self._wake.set()
        return _public(run)

    def select(self, job_id, material_id):
        with self.store.lock:
            before = self.store.meta(f'cv_selected:{job_id}')
            if material_id is not None:
                material = self.store.material(int(material_id))
                if material['job_id'] != int(job_id):
                    raise ValueError('This CV belongs to another job.')
                material_id = material['id']
            history = self.store.meta(f'cv_selection_history:{job_id}', [])
            if before != material_id:
                history.append({'before': before, 'after': material_id, 'at': _now()})
                self.store.put_meta(f'cv_selection_history:{job_id}', history)
                self.store.put_meta(f'cv_selected:{job_id}', material_id)
        return {'job_id': int(job_id), 'selected_material_id': material_id, 'previous_material_id': before}

    def _recover(self):
        with self.store.lock:
            for run in self._all():
                if run['status'] not in ACTIVE:
                    continue
                uncertain = any(r['status'] == 'dispatching' for r in run['receipts'].values())
                for receipt in run['receipts'].values():
                    if receipt['status'] == 'dispatching':
                        receipt['status'] = 'uncertain'
                run.update(status='uncertain' if uncertain else 'queued',
                    requires_explicit_retry=uncertain, label='Could not complete' if uncertain else 'Preparing draft',
                    error='The app restarted during a model call; explicitly retry to repeat that call.' if uncertain else None)
                self._write(run)

    def _work(self):
        while not self._closed:
            self._wake.wait(1)
            self._wake.clear()
            for run in self._all():
                if run['status'] == 'queued' and not self._closed:
                    self._run(run['id'])

    def close(self):
        self._closed = True
        for event in self._cancel_events.values():
            event.set()
        self._wake.set()

    def _stage(self, run, key, label):
        if self._cancel_events[run['id']].is_set():
            raise model_connections.ConnectionError('Cancelled between completed stages.', cancelled=True)
        run.update(stage=key, label=label)
        self._write(run)

    def _call(self, run, key, role, payload, schema):
        receipt = run['receipts'].get(key)
        if receipt and receipt['status'] == 'completed':
            if receipt.get('packet_hash') != _hash(payload):
                raise ValueError('The saved model result used different or unverifiable review input. Start a new Review Again run for this CV; this retry cannot combine different inputs.')
            return deepcopy(receipt['response'])
        connection = run['snapshot']['connections'][role]
        receipt = dict(receipt or {}, status='dispatching', role=role, started_at=_now(),
                       provider=connection['provider'], model=connection['model'], packet_hash=_hash(payload))
        run['receipts'][key] = receipt
        self._write(run)  # Commit the ambiguous boundary BEFORE invoking a provider.
        try:
            result = model_connections.execute(connection, payload, schema, role,
                cancel_event=self._cancel_events[run['id']], timeout_seconds=run['snapshot']['connections']['timeout_seconds'])
        except model_connections.ConnectionError as error:
            receipt.update(status='uncertain' if error.uncertain else 'failed', error=str(error), completed_at=_now())
            self._write(run)
            raise
        receipt.update(result, status='completed', completed_at=_now())
        receipt.pop('error', None)
        self._write(run)  # Store output immediately; deterministic validation can retry safely.
        with self.store.lock:
            live = self.store.meta('cv_model_live_receipts', {})
            live[role] = {key: receipt.get(key) for key in ('provider', 'model', 'actual_model', 'cli_version', 'completed_at')}
            # Test adapters must identify themselves and never create live evidence.
            if result.get('mocked') is not True:
                self.store.put_meta('cv_model_live_receipts', live)
        return deepcopy(result['response'])

    def _review_input(self, run, key, packet_builder, schema_builder):
        inputs = run.setdefault('review_inputs', {})
        if key not in inputs:
            inputs[key] = {'packet': deepcopy(packet_builder()), 'schema': deepcopy(schema_builder())}
            self._write(run)  # Freeze both reviewers' input before either dispatch.
        return deepcopy(inputs[key]['packet']), deepcopy(inputs[key]['schema'])

    def _save_material(self, run, branch, number, material, parent=None):
        material = deepcopy(material)
        material.pop('id', None)
        material.pop('job_id', None)
        material.update(cv_run_id=run['id'], cv_run_snapshot=run['snapshot_hash'], parent_id=parent,
                        base_cv_version=run['base_cv_version'], evidence_version=run['evidence_version'],
                        advert_version=run['advert_version'])
        value = self.store.save_material(run['job_id'], f'cv-run:{run["id"]}:{branch}:v{number}', material)
        if value['id'] not in run['material_ids']:
            run['material_ids'].append(value['id'])
        self._write(run)
        return value

    def _findings(self, run, branch, round_number, material, results):
        findings = []
        for result in results:
            for index, raw in enumerate(result.get('findings', [])):
                finding = deepcopy(raw)
                finding.update(id=f'{run["id"]}:{branch}:{round_number}:{result["team"]}:{index}', team=result['team'],
                    round=round_number, material_id=material['id'], type=raw.get('category'),
                    text=raw.get('recommended_action') or raw.get('cv_passage', ''),
                    evidence_refs=raw.get('evidence_ids', []), status='proposed')
                if raw.get('category') in {'experience_gap', 'question'}:
                    finding['status'] = 'needs_answer'
                findings.append(finding)
        known = {value['id']: value for value in run['suggestions']}
        for finding in findings:
            if finding['id'] not in known:
                run['suggestions'].append(finding)
        return findings

    def _branch(self, run, branch, direction):
        from careerops import cv_document
        snapshot = run['snapshot']
        job, profile, base = snapshot['job'], snapshot['profile'], snapshot['base_cv']
        source = snapshot.get('source_material')
        self._stage(run, 'draft', 'Preparing draft')
        if run['action'] == 'review_again':
            material = source
            if material['id'] not in run['material_ids']:
                run['material_ids'].append(material['id'])
        elif run['action'] == 'revise' and snapshot['selected_findings'] and not direction['instruction'] and direction['emphasis'] == source.get('direction', {}).get('emphasis', 'balanced'):
            material, decisions = cv_document.apply_findings(source, snapshot['selected_findings'], profile, job,
                                                            accepted_ids=run['selected_findings'])
            selected_by_id = {f['id']: f for f in snapshot['selected_findings']}
            run['what_improved'] = [selected_by_id.get(d.get('finding_id'), {}).get('recommended_action', d.get('reason', 'Applied a selected document change.')) for d in decisions if d.get('status') in {'applied', 'accepted'}]
            material = self._save_material(run, branch, 0, material, source['id'])
        else:
            packet = cv_document.generation_packet(job, profile, base, direction)
            if source:
                packet['previous_cv'] = {'sections': source.get('sections', []), 'direction': source.get('direction', {})}
                packet['selected_changes'] = snapshot['selected_findings']
            proposal = self._call(run, f'{branch}:generation', 'generator', packet, cv_document.generator_schema())
            material = cv_document.build_document(job, profile, proposal, direction=direction, base_cv=base)
            material = self._save_material(run, branch, 0, material, source['id'] if source else None)
            run['direction_reason'] = material.get('direction', {}).get('reason', '')
        for round_number in range(3):
            self._stage(run, 'evidence', 'Reviewing evidence')
            cv_document.validate_document(material, profile, job)
            self._stage(run, 'review', 'Independent review')
            # Durable inputs survive changed builders after a restart. Neither
            # reviewer sees its peer's response, including on a partial retry.
            packet, schema = self._review_input(run, f'{branch}:r{round_number}',
                lambda: cv_document.review_packet(job, profile, material, direction), cv_document.review_schema)
            results = []
            for team in ('red', 'blue'):
                response = self._call(run, f'{branch}:r{round_number}:{team}', team, deepcopy(packet), deepcopy(schema))
                results.append(cv_document.validate_review(response, job, profile, material, team))
            findings = self._findings(run, branch, round_number, material, results)
            review = {'branch': branch, 'round': round_number, 'material_id': material['id'],
                      'results': results, 'packet_hash': _hash(packet), 'status': 'completed'}
            run['reviews'] = [r for r in run['reviews'] if (r['branch'], r['round']) != (branch, round_number)] + [review]
            self._write(run)
            if round_number == 2 or run['action'] == 'review_again':
                break
            self._stage(run, 'improvements', 'Making improvements')
            revised, decisions = cv_document.apply_findings(material, findings, profile, job)
            if revised.get('cv_text') == material.get('cv_text'):
                break
            for decision in decisions:
                if decision.get('status') in {'applied', 'accepted'}:
                    finding = next((f for f in findings if f['id'] == decision.get('finding_id', decision.get('id'))), {})
                    run['what_improved'].append(str(finding.get('recommended_action', decision.get('reason', 'Applied an evidence-supported document change.'))))
            applied = {d.get('id', d.get('finding_id')) for d in decisions if d.get('status') in {'applied', 'accepted'}}
            for finding in run['suggestions']:
                if finding['id'] in applied:
                    finding['status'] = 'applied'
            material = self._save_material(run, branch, round_number + 1, revised, material['id'])
        return material

    def _compare(self, run, candidates):
        from careerops import cv_document
        snapshot = run['snapshot']
        def comparison_packet():
            packet = cv_document.review_packet(snapshot['job'], snapshot['profile'], candidates[0], snapshot['direction'])
            packet['comparison'] = [{'material_id': material['id'], 'sections': material.get('sections', []), 'direction': material.get('direction')} for material in candidates]
            packet['comparison_task'] = 'Prefer one version for this advert and explain evidence-linked differences. This is editorial preference, never a hiring-performance experiment.'
            return packet
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['material_id', 'reasons'],
                  'properties': {'material_id': {'type': 'integer', 'enum': [m['id'] for m in candidates]},
                                 'reasons': {'type': 'array', 'items': {'type': 'string'}}}}
        packet, schema = self._review_input(run, 'comparison', comparison_packet, lambda: schema)
        preferences = []
        for team in ('red', 'blue'):
            response = self._call(run, f'comparison:{team}', team, deepcopy(packet), deepcopy(schema))
            if response.get('material_id') not in [m['id'] for m in candidates] or not isinstance(response.get('reasons'), list):
                raise ValueError('Reviewer comparison did not identify an available CV.')
            preferences.append(dict(response, team=team))
        agreed = preferences[0]['material_id'] if preferences[0]['material_id'] == preferences[1]['material_id'] else None
        run['comparison'] = {'candidates': [{'material_id': m['id'], 'emphasis': m.get('direction', {}).get('emphasis'),
            'label': m.get('direction', {}).get('emphasis', 'CV')} for m in candidates], 'preferences': preferences,
            'recommended_material_id': agreed, 'summary': 'Both reviewers prefer the same version.' if agreed else 'The reviewers prefer different approaches; choose the version that best represents your direction.'}
        return agreed or candidates[0]['id']

    def _check_exports(self, run, material):
        from careerops.materials import export_material
        self._stage(run, 'document', 'Checking document')
        from careerops.materials import soffice_path
        from docx import Document
        docx = export_material(self.store, material['id'], 'docx')
        if not docx.is_file() or not any(p.text.strip() for p in Document(docx).paragraphs):
            raise ValueError('The exported document did not contain readable CV text.')
        checks = {'status': 'passed', 'docx_available': True, 'pdf_available': False,
                  'external_ats_tested': False}
        if soffice_path():
            from pypdf import PdfReader
            pdf = export_material(self.store, material['id'], 'pdf')
            reader = PdfReader(str(pdf))
            if not any((page.extract_text() or '').strip() for page in reader.pages):
                raise ValueError('The exported PDF did not contain readable CV text.')
            checks.update(pdf_available=True, pdf_pages=len(reader.pages), pdf_text_extractable=True)
            if len(reader.pages) > 2:
                checks['warning'] = 'This CV exceeds the normal two-page target; review its length before sending.'
        else:
            checks['warning'] = 'DOCX is ready. Install LibreOffice for PDF export.'
        run.setdefault('document_checks', {})[str(material['id'])] = checks

    def _run(self, run_id):
        with self.store.lock:
            run = self._read(run_id)
            if run['status'] != 'queued':
                return
            run.update(status='running', error=None)
            self._cancel_events[run['id']] = threading.Event()
            self._write(run)
        try:
            directions = [run['direction']]
            if run['action'] == 'compare':
                directions = [dict(run['direction'], emphasis='client_solutions'), dict(run['direction'], emphasis='technical_depth')]
            candidates = [self._branch(run, chr(97 + index), direction) for index, direction in enumerate(directions)]
            selected = self._compare(run, candidates) if len(candidates) == 2 else candidates[0]['id']
            for material in candidates:
                self._check_exports(run, material)
            run['selected_material_id'] = selected
            questions = any(f['category'] == 'question' for f in run['suggestions'])
            attention = [f['text'] for f in run['suggestions'] if f['category'] in {'experience_gap', 'question'}]
            for material in candidates:
                blocked = material.get('blocked_proposals', [])
                conflicts = material.get('base_cv_conflicts', [])
                questions = questions or bool(blocked or conflicts or material.get('requires_human_review'))
                attention.extend(str(p.get('reason', 'A proposed factual change needs confirmation.')) if isinstance(p, dict) else str(p) for p in blocked)
                attention.extend(('Base CV conflict: ' + str(c.get('message', c.get('reason', c.get('field', 'A protected fact needs confirmation.'))))) if isinstance(c, dict) else str(c) for c in conflicts)
                attention.extend(str(gap) for gap in material.get('analysis', {}).get('evidence_match', {}).get('gaps', []))
                warning = run.get('document_checks', {}).get(str(material['id']), {}).get('warning')
                if warning:
                    attention.append(warning)
            run['needs_attention'] = list(dict.fromkeys(attention))
            run.update(status='needs_answer' if questions else 'ready', stage='complete',
                       label='Needs your answer' if questions else 'Ready for your review', completed_at=_now())
            run['what_improved'] = list(dict.fromkeys(run['what_improved']))[:12]
            self._write(run)
            # Selection is local document preference, not an application action.
            self.select(run['job_id'], selected)
        except model_connections.ConnectionError as error:
            run.update(status='uncertain' if error.uncertain else 'cancelled' if error.cancelled else 'failed',
                stage='stopped', label='Could not complete' if not error.cancelled else 'Cancelled',
                error=str(error), requires_explicit_retry=error.uncertain)
            self._write(run)
        except Exception as error:
            # Candidate packets and arbitrary provider stderr are never logged or
            # sent to the browser. Completed receipts remain reusable on retry.
            run.update(status='failed', stage='stopped', label='Could not complete',
                       error=f'Document processing could not complete ({type(error).__name__}): {str(error)[:400]}. Completed model calls and earlier CV versions are preserved.',
                       requires_explicit_retry=False)
            self._write(run)
        finally:
            self._cancel_events.pop(run['id'], None)
