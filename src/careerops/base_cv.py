"""Immutable uploaded CV sources, reconciled without modifying verified evidence."""
from __future__ import annotations

import base64
import binascii
import calendar
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from careerops.store import digest, encode, now

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def extract(content, extension):
    if not 0 < len(content) <= MAX_UPLOAD_BYTES:
        raise ValueError("Choose a PDF or DOCX no larger than 8 MB.")
    try:
        if extension == '.docx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
                    raise ValueError("This Word document expands beyond the 30 MB reading limit.")
                xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError("Unsupported XML declarations in this document.")
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            root = ElementTree.fromstring(xml)
            # Paragraphs preserve reading order, including text in tables.
            lines = [''.join(n.text or '' for n in p.iter(ns + 't')) for p in root.iter(ns + 'p')]
        elif extension == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Use an unlocked PDF so its text can be read.")
            if len(reader.pages) > 30:
                raise ValueError("Use a CV with no more than 30 pages.")
            lines = '\n'.join(page.extract_text() or '' for page in reader.pages).splitlines()
        else:
            raise ValueError("Choose a PDF or DOCX file.")
    except ValueError:
        raise
    except Exception:
        raise ValueError("This file could not be read as a PDF or DOCX. Export a fresh copy and try again.") from None
    text = '\n'.join(line.strip() for line in lines if line.strip())
    if len(text) < 40:
        raise ValueError("No readable CV text was found. Upload a DOCX or a PDF with selectable text.")
    if len(text) > 200000:
        raise ValueError("This document contains too much text for a base CV.")
    return text


def _fold(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _date_range(text):
    month = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    date = rf'(?:(?:\d{{1,2}}\s+)?{month}\s+)?(?:19|20)\d{{2}}(?:-\d{{2}}(?:-\d{{2}})?)?'
    match = re.search(rf'\b({date})\s*(?:–|—|\s-\s|\s+to\s+)\s*({date}|present|current)\b', text, re.I)
    if not match:
        match = re.search(r'\b((?:19|20)\d{2})-((?:19|20)\d{2})\b', text)
    if not match:
        return None
    def normal(value):
        if re.fullmatch(r'\d{4}(?:-\d{2}){0,2}', value):
            return value
        year = re.search(r'(?:19|20)\d{2}', value)
        if not year:
            return value.lower()
        m = re.search(month, value, re.I)
        output = year.group()
        if m:
            number = list(calendar.month_abbr).index(m.group()[:3].title())
            output += f'-{number:02}'
            day = re.match(r'(\d{1,2})\s', value)
            if day:
                output += f'-{int(day.group(1)):02}'
        return output
    return tuple(normal(value) for value in match.groups())


def reconcile(text, profile):
    """Conservative source parsing: matching records are linked, new facts quarantined.

    ponytail: deterministic extraction recognises explicit fields; ambiguous free prose
    remains source text for review, rather than being promoted to verified facts.
    """
    lines = text.splitlines()
    sections, current = {}, 'profile'
    headings = {'profile': 'profile', 'professionalprofile': 'profile', 'summary': 'profile',
                'experience': 'employment', 'employment': 'employment', 'workexperience': 'employment',
                'employmenthistory': 'employment', 'professionalexperience': 'employment',
                'education': 'education', 'qualifications': 'education', 'educationqualifications': 'education',
                'skills': 'skills', 'technicalskills': 'skills', 'keyskills': 'skills',
                'projects': 'projects', 'selectedprojects': 'projects', 'research': 'research'}
    conflicts = []
    def conflict(field, quote, verified, message):
        item = {'field': field, 'source_quote': quote, 'verified_value': verified, 'message': message, 'status': 'needs_review'}
        item['id'] = digest(item)[:16]
        if item not in conflicts:
            conflicts.append(item)
    for line in lines:
        if _fold(line) in headings:
            current = headings[_fold(line)]
        else:
            sections.setdefault(current, []).append(line)
    contact = profile.get('contact') or {}
    emails = sorted(set(re.findall(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', text)))
    for value in emails:
        if contact.get('email') and value.casefold() != contact['email'].casefold():
            conflict('email', value, contact['email'], 'This email differs from the verified contact. The verified value is retained.')
    phones = list(dict.fromkeys(re.findall(r'(?<!\d)(?:\+44\s?\(?0?\)?|0)\d[\d ()-]{8,17}\d(?!\d)', '\n'.join(lines[:15]))))
    def phone(value):
        digits = re.sub(r'\D', '', value)
        return '0' + digits[2:] if digits.startswith('44') else digits
    for value in phones:
        if contact.get('phone') and phone(value) != phone(contact['phone']):
            conflict('phone', value, contact['phone'], 'This number differs from the verified contact. The verified value is retained.')
    name = profile.get('name', '')
    # Middle names and display-name punctuation are not competing identities.
    if name and lines and re.fullmatch(r"[A-Za-zÀ-ž .'-]{5,80}", lines[0]) and _fold(lines[0]) not in headings:
        expected = re.findall(r'[a-z]+', name.lower())
        actual = re.findall(r'[a-z]+', lines[0].lower())
        if not set(expected).issubset(actual) and not any(word in lines[0].lower() for word in ['curriculum', 'resume', 'engineer', 'analyst']):
            conflict('name', lines[0], name, 'The name on this document differs from the verified identity.')
    linked = []
    for kind in ('employment', 'education', 'projects', 'research'):
        for record in profile.get(kind, []):
            keys = [record.get(k) for k in ('employer', 'title', 'qualification', 'name') if record.get(k)]
            indexes = [i for i, line in enumerate(lines) if any(_fold(key) in _fold(line) for key in keys if len(_fold(key)) >= 6)]
            if not indexes:
                continue
            quotes = list(dict.fromkeys(lines[i] for i in indexes))
            linked.append({'kind': kind, 'record_id': record.get('record_id'), 'source_quotes': quotes})
            if kind == 'employment':
                for i in indexes:
                    surrounding = ' '.join(lines[max(0, i-1):i+2])
                    observed = _date_range(surrounding)
                    if observed and record.get('start') and record.get('end'):
                        original = str(record['start']), str(record['end'])
                        verified = _date_range(' – '.join(original)) or original
                        qualifier_pattern = r'\b(early|mid|late)\s+((?:19|20)\d{2})\b'
                        source_qualifiers = {year: part.lower() for part, year in re.findall(qualifier_pattern, surrounding, re.I)}
                        verified_qualifiers = {year: part.lower() for part, year in re.findall(qualifier_pattern, ' – '.join(original), re.I)}
                        qualifier_conflict = any(source_qualifiers[year] != verified_qualifiers[year] for year in source_qualifiers.keys() & verified_qualifiers.keys())
                        if qualifier_conflict or any(actual[:min(len(actual),len(known))] != known[:min(len(actual),len(known))] for actual, known in zip(observed, verified)):
                            conflict('employment_dates', surrounding, ' – '.join(original), 'The dates near ' + str(record.get('employer') or record.get('title')) + ' differ from the verified employment record.')
                employer = record.get('employer')
                for i in indexes:
                    line = lines[i]
                    if employer and record.get('title') and _fold(record['title']) in _fold(line) and re.search(r'\s(?:at|@)\s', line):
                        stated = re.split(r'\s(?:at|@)\s', line, maxsplit=1)[1]
                        if _fold(employer) not in _fold(stated):
                            conflict('employer', line, employer, 'The employer attached to this role needs checking against the verified record.')
    for line in lines:
        if re.search(r'\b(?:MSc|M\.Sc\.?|PhD|PGDip|Master(?:[’\']s)? (?:degree|of))\b', line, re.I) and not re.search(r'\b(?:not (?:completed|awarded)|incomplete|within|programme|program|towards|candidate|module|research)\b', line, re.I):
            known = ' '.join(str(r.get('qualification') or '') for r in profile.get('education', []))
            if profile.get('education') and not any(_fold(line) == _fold(r.get('qualification')) for r in profile['education']):
                conflict('qualification', line, known, 'This qualification wording may imply an award not established by the verified education record.')
    return {'sections': sections, 'contact': {'emails': emails, 'phones': phones},
            'linked_records': linked, 'source_only': True,
            'note': 'Extracted source text is not automatically added to verified evidence.'}, conflicts


def selected(store):
    version_id = store.meta('selected_base_cv_id')
    if version_id is None:
        return None
    with store.connect() as db:
        row = db.execute('SELECT data FROM base_documents WHERE id=?', (version_id,)).fetchone()
    if not row:
        return None
    document = json.loads(row[0])
    # Reconciliation is a current view; immutable sources and frozen runs stay intact.
    document['structured'], document['conflicts'] = reconcile(document['text'], store.profile())
    return document


def workspace(store):
    with store.connect() as db:
        documents = [json.loads(row[0]) for row in db.execute('SELECT data FROM base_documents ORDER BY id DESC')]
    return {'selected': selected(store), 'versions': [{k:v for k,v in doc.items() if k not in {'text', 'structured', 'preview'}} for doc in documents],
            'needs_setup': not bool(selected(store)), 'max_upload_bytes': MAX_UPLOAD_BYTES,
            'warning': store.meta('base_cv_import_error') if not selected(store) else None}


def select(store, version_id):
    if isinstance(version_id, bool) or not isinstance(version_id, int):
        raise ValueError('Choose a stored base CV version.')
    with store.lock, store.connect() as db:
        if not db.execute('SELECT 1 FROM base_documents WHERE id=?', (version_id,)).fetchone():
            raise ValueError('This base CV version does not exist.')
        store.put_meta('selected_base_cv_id', version_id)
    return workspace(store)


def add(store, filename, content, source='upload'):
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 240 or any(c in filename for c in '\r\n\x00'):
        raise ValueError('Choose a file with an ordinary filename.')
    filename = filename.replace('\\', '/').split('/')[-1]
    extension = Path(filename).suffix.lower()
    text = extract(content, extension)
    sha = hashlib.sha256(content).hexdigest()
    structured, conflicts = reconcile(text, store.profile())
    with store.lock, store.connect() as db:
        existing = db.execute('SELECT id FROM base_documents WHERE sha256=?', (sha,)).fetchone()
        if existing:
            version_id = existing[0]
        else:
            folder = store.path.parent / 'base_documents'
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = folder / (sha + extension)
            if target.exists() and target.read_bytes() != content:
                raise ValueError('Stored source integrity check failed. Existing documents are preserved.')
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(content)
                target.chmod(0o600)
            document = {'filename': filename, 'sha256': sha, 'uploaded_at': now(), 'source': source,
                        'text': text, 'preview': text, 'structured': structured, 'conflicts': conflicts,
                        'evidence_version': digest(store.profile()), 'original_preserved': True}
            row = db.execute('INSERT INTO base_documents(sha256,data) VALUES (?,?)', (sha, encode(document)))
            version_id = row.lastrowid
            document['id'] = version_id
            db.execute('UPDATE base_documents SET data=? WHERE id=?', (encode(document), version_id))
        db.execute("INSERT INTO metadata(key,data) VALUES ('selected_base_cv_id',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (encode(version_id),))
    return workspace(store)


def upload(store, data):
    encoded = data.get('content_base64')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
        raise ValueError('Choose a PDF or DOCX no larger than 8 MB.')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('The uploaded file could not be decoded. Choose it again.') from None
    return add(store, data.get('filename'), content)
