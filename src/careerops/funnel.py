"""Inspectable counts. Stages overlap intentionally; inventories are not probabilities."""
from collections import Counter


def funnel(jobs, settings, shortlist=None, runs=None):
    from careerops.inventory import classify_job, inventory_diagnostics
    top_ids = {j['id'] for group in (shortlist or {}).values() for j in group}
    groups = {}
    annotated = [(j, classify_job(j, settings)) for j in jobs]
    for region in ('overseas', 'london', 'other'):
        rows = [(j, i) for j, i in annotated if i['region'] == region]
        by_reason = Counter()
        for job, info in rows:
            for reason in info['exclusion_reasons']:
                by_reason[reason] += 1
        groups[region] = {
            'persisted': len(rows), 'historical_imported': sum(bool(j.get('legacy')) for j, _ in rows),
            'discovered_nonhistorical': sum(not j.get('legacy') for j, _ in rows),
            'verified_open': sum(i['verification'] == 'verified_open' for _, i in rows),
            'verification_pending': sum(i['verification'] == 'pending' for _, i in rows),
            'confirmed_closed': sum(i['verification'] == 'closed' for _, i in rows),
            'confirmed_blocked': sum(i['eligibility'] == 'blocked' for _, i in rows),
            'applied': sum(j['status'] in {'applied','interview','offer'} for j, _ in rows),
            'dismissed': sum(j['status'] == 'dismissed' for j, _ in rows),
            'low_soft_fit': sum(i['fit_group'] == 'low' for _, i in rows),
            'old_score_gate_admitted': sum(bool(j.get('evaluation', {}).get('admitted')) for j, _ in rows),
            'broad_accessible': sum(i['accessible'] for _, i in rows),
            'indexed': sum(i['indexed'] for _, i in rows),
            'universe_visible': sum(i['universe_visible'] for _, i in rows),
            'recommended': sum(i['recommended'] for _, i in rows),
            'top_picks_displayed': sum(j['id'] in top_ids for j, _ in rows),
            'outside_top_picks_but_accessible': sum(i['accessible'] and j['id'] not in top_ids for j, i in rows),
            'countries': dict(Counter(c for _, i in rows for c in i['countries'])),
            'role_families': dict(Counter(i['role_family'] for _, i in rows)),
            'exclusion_reasons': dict(by_reason),
        }
    return {'regions': groups, 'total_persisted': len(jobs), 'runs': runs or [],
            'universe': inventory_diagnostics(jobs, settings, runs),
            'note': 'Counts are explicitly overlapping: a historical job may also be closed or applied. Verified open means an authoritative public listing was retrieved; eligibility is separate.'}
