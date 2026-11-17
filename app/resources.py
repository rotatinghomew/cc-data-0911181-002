from .storage import DataError


def activate(store, journal, payload):
    with journal.transaction():
        row = journal.get('nomination', payload['nomination'])
        grant = journal.get('grant', row['nomination'])
        resource = store.find('available_resources', resource=row['resource'])
        if grant['status'] != 'approved':
            raise DataError('grant not approved')
        if grant['proposal'] != row['proposal'] or grant['resource'] != row['resource']:
            raise DataError('resource commitment does not match grant target')
        if row['seats'] > resource['seats']:
            raise DataError('resource does not cover proposal')
        for active in journal.all('active_site'):
            # 多个方案可以共享同一处资源，但测算与拨付都不得先占用它：
            # 一旦被某个获批方案启用，其他提名不能重复占用。
            if active['resource'] == row['resource'] \
                    and active['nomination'] != row['nomination']:
                raise DataError('resource already committed')
        active_row = dict(row, status='active', grant_amount=grant['amount'],
                          beneficiary_groups=list(grant['beneficiary_groups']))
        return journal.put('active_site', row['nomination'], active_row)
