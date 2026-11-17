from .storage import DataError


def approve(store,journal,payload):
    with journal.transaction():
        row=journal.get('nomination',payload['nomination'])
        amount=float(payload['amount'])
        policy=store.find('funding_rules',rule='county')
        if row['status']!='proposed' or not 0<amount<=policy['maximum_per_site']:
            raise DataError('invalid grant')
        grants=journal.all('grant')
        if any(item['nomination']==row['nomination'] for item in grants):
            return next(item for item in grants if item['nomination']==row['nomination'])
        if sum(item['amount'] for item in grants)+amount>policy['budget']:
            raise DataError('budget exceeded')
        return journal.put('grant',row['nomination'],dict(nomination=row['nomination'],amount=amount,status='approved'))
