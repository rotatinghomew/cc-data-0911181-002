from . import accessibility
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


def _proposal_filter(query):
    raw = single(query, 'proposals', default=None)
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(',') if part.strip()]
    if not values:
        from .storage import DataError
        raise DataError('proposals must contain at least one id')
    return values


def access(store, query):
    travel = single(query, 'travel', default='base')
    population = single(query, 'population', default='base')
    proposal_ids = _proposal_filter(query) or ()
    return accessibility.evaluate_scenario(
        store, proposal_ids, travel, population)


def proposal_comparison(store, query):
    proposal_ids = _proposal_filter(query)
    return accessibility.compare_proposals(store, proposal_ids)


ROUTES = {'/api/reach': reach,
          '/api/capacity': capacity,
          '/api/accessibility': access,
          '/api/proposal-comparison': proposal_comparison}
