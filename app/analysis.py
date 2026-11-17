from .domain import nearest_site, eligible_offers
from .storage import single


def reach(store, query):
    village = single(query, 'village')
    groups = [group for group in store.table('groups')
              if village is None or group['village'] == village]
    return {'groups': [dict(group=group['group'], population=group['population'],
                            nearest=nearest_site(store, group),
                            eligible=eligible_offers(store, group)) for group in groups],
            'meaning': 'individual_reachability_without_allocation'}


def capacity(store, query):
    offers = store.table('offers')
    return {'offers': offers, 'seats': sum(offer['seats'] for offer in offers),
            'proposals': store.table('proposals')}


ROUTES = {'/api/reach': reach, '/api/capacity': capacity}
