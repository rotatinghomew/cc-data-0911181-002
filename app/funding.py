from .storage import DataError


def _has_service_evidence(evidence):
    # 新增服务或地区改善，只要任一情景区间为正即保留为可核定，
    # 但区间明细同时落库，防止把区间结果当成确定数值。
    return (any(value > 0 for value in evidence['gain_cells'])
            or any(value > 0 for value in evidence['gap_improvement_cells']))


def approve(store, journal, payload):
    with journal.transaction():
        row = journal.get('nomination', payload['nomination'])
        amount = float(payload['amount'])
        policy = store.find('funding_rules', rule='county')
        if row['status'] != 'proposed' or not 0 < amount <= policy['maximum_per_site']:
            raise DataError('invalid grant')
        # 数据指纹或拟投范围变化后，旧比较依据不得继续用于拨付。
        if row['basis_fingerprint'] != store.fingerprint():
            raise DataError('comparison basis no longer valid for current dataset')
        basis = journal.get('comparison', row['basis'])
        if basis['proposals'] != row['basis_proposals']:
            raise DataError('comparison basis scope has changed')
        evidence = row['evidence']
        if not _has_service_evidence(evidence):
            raise DataError('no new service or equity evidence for grant')
        grants = journal.all('grant')
        if any(item['nomination'] == row['nomination'] for item in grants):
            return next(item for item in grants
                        if item['nomination'] == row['nomination'])
        # 同一批人不能因为被多个点覆盖而重复申请补助。
        prior_groups = set()
        for item in grants:
            prior_groups.update(item['beneficiary_groups'])
        overlap = prior_groups.intersection(evidence['affected_groups'])
        if overlap:
            raise DataError('duplicate benefit on groups: '
                            + ','.join(sorted(overlap)))
        if sum(item['amount'] for item in grants) + amount > policy['budget']:
            raise DataError('budget exceeded')
        grant = dict(nomination=row['nomination'], amount=amount,
                     status='approved', basis=row['basis'],
                     basis_fingerprint=row['basis_fingerprint'],
                     proposal=row['proposal'], resource=row['resource'],
                     beneficiary_groups=list(evidence['affected_groups']),
                     beneficiary_detail=evidence['affected_detail'],
                     gain_range=evidence['gain_range'],
                     substitution_groups=evidence['substitution_groups'])
        return journal.put('grant', row['nomination'], grant)
