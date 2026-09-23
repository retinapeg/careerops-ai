import base64
import io
import zipfile

import pytest

from careerops import base_cv
from careerops.store import Store, digest


def docx(text):
    from xml.sax.saxutils import escape
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + ''.join('<w:p><w:r><w:t>' + escape(s) + '</w:t></w:r></w:p>' for s in text.splitlines()) + '</w:body></w:document>')
    return output.getvalue()


def test_upload_reuse_and_replacement_preserve_profile_and_original(tmp_path):
    store = Store(tmp_path / 'app.sqlite3')
    profile = store.profile()
    profile.update(name='Candidate Example', contact={'email': 'candidate@example.org', 'phone': '07700900001'},
                   employment=[{'record_id': 'one', 'title': 'Support Analyst', 'employer': 'Example Ltd', 'start': '2020-01', 'end': '2022-06'}],
                   education=[{'qualification': 'Bachelor of Science in Statistics'}])
    store.update_profile(profile)
    before = digest(store.profile())
    content = docx('Candidate Example\ncandidate@example.org\nExperience\nSupport Analyst at Example Ltd\n2020–2022\nSkills\nPython and quantitative analysis')
    payload = {'filename': '../original.docx', 'content_base64': base64.b64encode(content).decode()}
    first = base_cv.upload(store, payload)['selected']
    assert first['filename'] == 'original.docx'
    assert not first['conflicts']
    assert first['structured']['linked_records'][0]['record_id'] == 'one'
    assert base_cv.upload(store, payload)['selected']['id'] == first['id']
    second = base_cv.add(store, 'replacement.docx', docx('Another Person\nwrong@example.org\n07700900002\nMSc Statistics\nSupport Analyst at Different Ltd\n2019–2024'))['selected']
    assert {'name','email','phone','qualification','employment_dates','employer'} <= {c['field'] for c in second['conflicts']}
    assert digest(store.profile()) == before
    assert len(base_cv.workspace(store)['versions']) == 2
    assert (tmp_path / 'base_documents' / (first['sha256'] + '.docx')).read_bytes() == content
    assert base_cv.select(store, first['id'])['selected'] == first
    with pytest.raises(ValueError):
        base_cv.select(store, 999)
    with pytest.raises(ValueError):
        base_cv.upload(store, dict(payload, content_base64='not base64'))
    with pytest.raises(ValueError):
        base_cv.add(store, 'wrong.pdf', content)
    with pytest.raises(ValueError):
        base_cv.add(store, 'empty.docx', docx('empty'))


def test_section_text_and_middle_names_are_not_false_conflicts():
    text = 'Candidate Middle Example\nEducation\nGraduate Diploma in Economics within the Master of Arts programme\nExperience\nSupported scientific research with Python.'
    structured, conflicts = base_cv.reconcile(text, {'name': 'Candidate Example', 'education': [{'qualification': 'Graduate Diploma in Economics'}]})
    assert not conflicts
    assert structured['sections']['education'] == ['Graduate Diploma in Economics within the Master of Arts programme']
    assert base_cv._date_range('Sep 2023 – November 2024') == ('2023-09', '2024-11')
    assert base_cv._date_range('12 Jan 2026 – 26 Jun 2026') == ('2026-01-12', '2026-06-26')
    profile = {'employment': [{'title': 'Data Scientist', 'employer': 'Example Ltd', 'start': 'Late 2020', 'end': '2021-02'}]}
    assert not base_cv.reconcile('Data Scientist | Example Ltd | Late 2020 – Feb 2021', profile)[1]
    assert base_cv.reconcile('Data Scientist | Example Ltd | Late 2019 – Feb 2021', profile)[1]
    assert base_cv.reconcile('Data Scientist | Example Ltd | Early 2020 – Feb 2021', profile)[1]


def test_needs_checking_keeps_low_fit_unknown_roles():
    from careerops.inventory import query_inventory
    from careerops.policy import default_settings
    jobs = [{'id': 1, 'title': 'Retail support', 'location': 'Tel Aviv, Israel', 'country': 'IL', 'evaluation': {'eligibility': 'needs_checking', 'fit': 2}, 'last_verified': '2026-09-11'}]
    result = query_inventory(jobs, default_settings(), {'view': 'needs_checking', 'region': 'all'})
    assert result['all_matching_ids'] == [1]
    assert result['per_page'] == 50


def test_location_presets_are_generic_and_countries_use_the_country_filter():
    from careerops.inventory import query_inventory
    from careerops.policy import default_settings
    base = {'title': 'Data analyst', 'evaluation': {'eligibility': 'needs_checking', 'fit': 2}, 'last_verified': '2026-09-11'}
    jobs = [dict(base, id=1, location='London, UK', country='GB', work_pattern='hybrid'),
            dict(base, id=2, location='Athens, Greece', country='GR', work_pattern='remote')]
    ids = lambda location: query_inventory(jobs, default_settings(), {'view': 'needs_checking', 'region': 'all', 'location': location})['all_matching_ids']
    assert ids('') == [1, 2] and ids('london') == [1] and ids('remote') == [2]
    with pytest.raises(ValueError, match='Unsupported location preset'):
        ids('greece')


def test_readable_titles_and_literal_language_requirements():
    from careerops.inventory import presentation
    job = {'title': 'AI Trainers Network - Greek', 'company':'Example', 'location':'Greece',
           'description': 'This is not an active job opening.\nNative or near-native fluency in Greek\nEnglish proficiency', 'url':'https://example.org/job'}
    shown = presentation(job)
    assert shown['title'] == 'AI Trainers Network'
    assert shown['language'][0]['label'] == 'Greek required — native or near-native. Your matching fluency is unconfirmed.'
    assert shown['notice'] == 'Talent network — no current vacancy promised.'
    assert job['title'].endswith(' - Greek')
    assert presentation(dict(job,title='AI Trainer - Galician',description='Native or near-native fluency in Galician'))['language'][0]['language'] == 'Galician'
    assert presentation(dict(job,description='Remote role based in Greece'))['language'][0]['requirement'] == 'unconfirmed'
    assert presentation(dict(job,title='Engineer - Trading Systems'))['title'] == 'Engineer - Trading Systems'
    assert presentation(dict(job,title='Engineer - Greece - Remote'))['title'] == 'Engineer'
    assert presentation(dict(job,title='Engineer - [placeholder]'))['title'] == 'Engineer'
    assert presentation(dict(job,title='Engineer - UK',location='London, United Kingdom'))['title'] == 'Engineer'
    stacked = presentation(dict(job,title='Engineer - Greek - Remote',description='No language information supplied.'))
    assert stacked['title'] == 'Engineer'
    assert stacked['language'][0]['requirement'] == 'unconfirmed'
    from careerops.server import Application
    assert Application.summary(dict(job,evaluation={'blockers':['Native fluency required']}))['evaluation']['blockers'] == ['Native fluency required']
    optional = presentation(dict(job,title='Engineer',description='English required; French fluency is optional.'))
    assert next(n for n in optional['language'] if n['language'] == 'French')['requirement'] == 'not_required'
    mixed = presentation(dict(job,title='Engineer',description='English fluency is required and French is optional.'))
    assert all(n['requirement'] == 'unconfirmed' for n in mixed['language'])
