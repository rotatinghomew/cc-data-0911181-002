def nominate(store,journal,payload):
    proposal=store.find('proposals',proposal=payload['proposal'])
    row=dict(nomination=payload['nomination'],proposal=proposal['proposal'],site=proposal['site'],
             seats=proposal['seats'],resource=payload['resource'],status='proposed')
    with journal.transaction():
        return journal.put('nomination',row['nomination'],row)
