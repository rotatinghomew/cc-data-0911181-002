def nearest_site(store, group):
    current_sites = {offer['site'] for offer in store.table('offers')}
    routes = [row for row in store.select('routes', group=group['group'])
              if row['site'] in current_sites]
    if not routes:
        return None
    route = min(routes, key=lambda row: (row['minutes'], row['site']))
    return dict(site=route['site'], minutes=route['minutes'])


def eligible_offers(store, group):
    routes = {row['site']: row for row in store.select('routes', group=group['group'])}
    return [offer['offer'] for offer in store.table('offers')
            if offer['slot'] in group['slots'] and offer['site'] in routes
            and routes[offer['site']]['minutes'] <= group['maximum_minutes']]
