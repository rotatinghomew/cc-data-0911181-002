from hashlib import sha256
from . import accessibility
from .storage import DataError


def _basis_id(comparison):
    payload = (comparison['dataset_fingerprint'] + '|'
               + ','.join(comparison['proposals']))
    return 'basis-' + sha256(payload.encode()).hexdigest()[:16]


def compare(store, journal, payload):
    """生成比较依据并落库；同一数据指纹与拟投范围得到同一依据编号。"""
    raw = payload.get('proposals')
    if raw is None:
        proposal_ids = [row['proposal'] for row in store.table('proposals')]
    elif isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        proposal_ids = raw
    else:
        raise DataError('proposals must be a nonempty list of proposal ids')
    comparison = accessibility.compare_proposals(store, proposal_ids)
    basis_id = _basis_id(comparison)
    row = dict(basis=basis_id,
               dataset_fingerprint=comparison['dataset_fingerprint'],
               proposals=list(comparison['proposals']),
               comparison=comparison)
    with journal.transaction():
        return journal.put('comparison', basis_id, row)


def _evidence(comparison, proposal_id):
    if proposal_id not in comparison['proposals']:
        raise DataError('proposal outside comparison basis: ' + proposal_id)
    affected = comparison['comparison']['affected'][proposal_id]
    substitution = comparison['comparison']['substitution'][proposal_id]
    base_cells = comparison['comparison']['scenarios'][proposal_id]['cells']
    baseline_cells = comparison['comparison']['scenarios']['baseline']['cells']
    gain_cells = [base_cells[key]['served_total']
                  - baseline_cells[key]['served_total']
                  for key in base_cells]
    gap_cells = [baseline_cells[key]['disparity']['village_rate_gap']
                 - base_cells[key]['disparity']['village_rate_gap']
                 for key in base_cells]
    return {
        'proposal': proposal_id,
        'affected_groups': [item['group'] for item in affected['groups']],
        'affected_detail': affected['groups'],
        'substitution_groups': [item['group'] for item in substitution['groups']],
        'substitution_detail': substitution['groups'],
        'gain_base': affected['population_gain_base'],
        'gain_range': affected['population_gain_range'],
        'gain_cells': gain_cells,
        'gap_improvement_cells': gap_cells,
    }


def nominate(store, journal, payload):
    proposal = store.find('proposals', proposal=payload['proposal'])
    nomination_id = payload['nomination']
    with journal.transaction():
        existing = journal.peek('nomination', nomination_id)
        if existing is not None:
            raise DataError('nomination already exists: ' + nomination_id)
        basis = journal.get('comparison', payload['basis'])
        if basis['dataset_fingerprint'] != store.fingerprint():
            raise DataError('comparison basis is stale for current dataset')
        if payload['proposal'] not in basis['proposals']:
            raise DataError('proposal outside comparison basis scope')
        resource = store.find('available_resources',
                              resource=payload['resource'])
        if proposal['seats'] > resource['seats']:
            raise DataError('resource does not cover proposal seats')
        evidence = _evidence(basis, proposal['proposal'])
        row = dict(nomination=nomination_id, proposal=proposal['proposal'],
                   site=proposal['site'], seats=proposal['seats'],
                   resource=payload['resource'], status='proposed',
                   basis=basis['basis'],
                   basis_fingerprint=basis['dataset_fingerprint'],
                   basis_proposals=list(basis['proposals']),
                   evidence=evidence)
        return journal.put('nomination', nomination_id, row)
