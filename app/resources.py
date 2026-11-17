from .storage import DataError


def activate(store,journal,payload):
    with journal.transaction():
        row=journal.get('nomination',payload['nomination'])
        grant=journal.get('grant',row['nomination'])
        resource=store.find('available_resources',resource=row['resource'])
        if grant['status']!='approved' or row['seats']>resource['seats']:
            raise DataError('resource does not cover proposal')
        for active in journal.all('active_site'):
            if active['resource']==row['resource'] and active['nomination']!=row['nomination']:
                raise DataError('resource already committed')
        return journal.put('active_site',row['nomination'],dict(row,status='active'))
