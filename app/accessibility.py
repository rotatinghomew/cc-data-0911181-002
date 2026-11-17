"""扩点讨论用的可及性分析。

关键口径：
- “能到达学校”不等于“在自己能参加的时间有合适课程”，资格同时受
  出行上限（按 mobility 分组的 maximum_minutes）、可参加时段 slots、
  课程余位 seats 约束；
- 共享余位通过二部网络最大流测算，输出的是可服务人数的能力边界，
  不是某一次确定的报名分配；
- 人口与出行耗时均有调查区间，在 3x3 情景区间上比较，不输出看似精确的排名；
- 同一居民组被多个点覆盖只计一次，新增服务按情景差值识别并保留受影响人群明细。
"""

from .flow import FlowNetwork
from .storage import DataError

TRAVEL_LEVELS = ('low', 'base', 'high')
POPULATION_LEVELS = ('low', 'base', 'high')
WEIGHT_SCALE = 1000

REASON_LABELS = {
    'no_reachable_site': '出行上限内没有可达学校',
    'no_course_nearby': '可达学校没有开班课程',
    'no_time_slot_match': '可达课程的时段与本人可参加时段不合',
    'capacity_constrained': '有合适课程但共享余位不足',
    'fully_served': '本组在该情景下可全部服务',
}

SOURCE = 'source'
SINK = 'sink'


def _require_level(value, allowed, name):
    if value not in allowed:
        raise DataError(name + ' must be one of ' + ','.join(allowed))
    return value


def _route_minutes(route, travel_level):
    return {'low': route['minutes_low'],
            'base': route['minutes'],
            'high': route['minutes_high']}[travel_level]


def _group_population(group, population_level):
    return {'low': group['population_low'],
            'base': group['population'],
            'high': group['population_high']}[population_level]


def _build_offers(store, proposal_ids):
    offers = []
    for row in store.table('offers'):
        offers.append(dict(offer=row['offer'], site=row['site'], topic=row['topic'],
                           slot=row['slot'], seats=row['seats'],
                           source='existing', proposal=None))
    for proposal_id in proposal_ids:
        row = store.find('proposals', proposal=proposal_id)
        offers.append(dict(offer='proposal:' + row['proposal'], site=row['site'],
                           topic=row['topic'], slot=row['slot'], seats=row['seats'],
                           source='proposal', proposal=row['proposal']))
    return offers


def _preference_index(store):
    index = {}
    for row in store.table('preferences'):
        index[(row['group'], row['topic'])] = row['weight']
    return index


def eligibility_catalog(store, travel_level='base', proposal_ids=()):
    """按出行区间给出每个居民组的可达学校与合格课程明细（不含余位分配）。"""
    _require_level(travel_level, TRAVEL_LEVELS, 'travel')
    offers = _build_offers(store, tuple(proposal_ids))
    offers_by_site = {}
    for offer in offers:
        offers_by_site.setdefault(offer['site'], []).append(offer)
    preferences = _preference_index(store)
    catalog = {}
    for group in store.table('groups'):
        routes = store.select('routes', group=group['group'])
        reachable_sites = []
        for route in sorted(routes, key=lambda item: (item['site'],)):
            minutes = _route_minutes(route, travel_level)
            if minutes <= group['maximum_minutes']:
                reachable_sites.append({'site': route['site'], 'minutes': minutes})
        reachable_offers = [offer for site in reachable_sites
                            for offer in offers_by_site.get(site['site'], ())]
        slot_offers = [offer for offer in reachable_offers
                       if offer['slot'] in group['slots']]
        minutes_by_site = {item['site']: item['minutes'] for item in reachable_sites}
        eligible = []
        for offer in slot_offers:
            eligible.append(dict(offer=offer['offer'], site=offer['site'],
                                 topic=offer['topic'], slot=offer['slot'],
                                 source=offer['source'], proposal=offer['proposal'],
                                 minutes=minutes_by_site[offer['site']],
                                 preference_weight=preferences.get(
                                     (group['group'], offer['topic']), 0)))
        catalog[group['group']] = {
            'reachable_sites': reachable_sites,
            'reachable_offers': sorted(offer['offer'] for offer in reachable_offers),
            'slot_matching_offers': sorted(offer['offer'] for offer in slot_offers),
            'eligible': eligible,
        }
    return catalog


def _unserved_reason(catalog_entry):
    if not catalog_entry['reachable_sites']:
        return 'no_reachable_site'
    if not catalog_entry['reachable_offers']:
        return 'no_course_nearby'
    if not catalog_entry['slot_matching_offers']:
        return 'no_time_slot_match'
    return 'capacity_constrained'


def evaluate_scenario(store, proposal_ids=(), travel_level='base',
                      population_level='base'):
    """测算单个情景：资格判定 + 共享余位最大流 + 未服务原因与地区差距。"""
    _require_level(travel_level, TRAVEL_LEVELS, 'travel')
    _require_level(population_level, POPULATION_LEVELS, 'population')
    proposal_ids = tuple(proposal_ids)
    groups = store.table('groups')
    catalog = eligibility_catalog(store, travel_level, proposal_ids)
    offers = _build_offers(store, proposal_ids)
    populations = {group['group']: _group_population(group, population_level)
                   for group in groups}

    network = FlowNetwork()
    for offer in offers:
        network.add_edge(offer['offer'], SINK, offer['seats'], cost=0)
    for group in groups:
        network.add_edge(SOURCE, group['group'], populations[group['group']], cost=0)
        seen = set()
        for entry in catalog[group['group']]['eligible']:
            if entry['offer'] in seen:
                continue
            seen.add(entry['offer'])
            cost = -round(entry['preference_weight'] * WEIGHT_SCALE)
            network.add_edge(group['group'], entry['offer'],
                             populations[group['group']], cost=cost)

    served_flow, flow_cost = network.min_cost_max_flow(SOURCE, SINK)
    group_flows = network.edge_flows(SOURCE)
    offer_flows = network.inflow(SINK)

    group_rows = []
    reason_population = {}
    village_stats = {}
    mobility_stats = {}
    preference_potential = 0.0
    for group in groups:
        gid = group['group']
        population = populations[gid]
        served = group_flows.get(gid, 0)
        unserved = population - served
        entry = catalog[gid]
        if unserved == 0:
            reason = 'fully_served'
        else:
            reason = _unserved_reason(entry)
        reason_population.setdefault(reason, {'groups': [], 'population': 0})
        reason_population[reason]['groups'].append(gid)
        reason_population[reason]['population'] += unserved
        best_weight = max((item['preference_weight']
                           for item in entry['eligible']), default=0)
        preference_potential += best_weight * population
        row = dict(group=gid, village=group['village'], mobility=group['mobility'],
                   maximum_minutes=group['maximum_minutes'], slots=list(group['slots']),
                   population=population, served=served, unserved=unserved,
                   reason=reason, reason_label=REASON_LABELS[reason],
                   reachable_sites=entry['reachable_sites'],
                   reachable_offers=entry['reachable_offers'],
                   slot_matching_offers=entry['slot_matching_offers'],
                   eligible_offers=entry['eligible'])
        group_rows.append(row)
        stats = village_stats.setdefault(group['village'],
                                         {'village': group['village'],
                                          'population': 0, 'served': 0})
        stats['population'] += population
        stats['served'] += served
        mob = mobility_stats.setdefault(group['mobility'],
                                        {'mobility': group['mobility'],
                                         'population': 0, 'served': 0})
        mob['population'] += population
        mob['served'] += served

    village_rows = []
    for village in sorted(village_stats):
        stats = village_stats[village]
        rate = stats['served'] / stats['population'] if stats['population'] else 0.0
        stats['unserved'] = stats['population'] - stats['served']
        stats['rate'] = round(rate, 4)
        village_rows.append(stats)
    mobility_rows = []
    for mobility in sorted(mobility_stats):
        stats = mobility_stats[mobility]
        rate = stats['served'] / stats['population'] if stats['population'] else 0.0
        stats['unserved'] = stats['population'] - stats['served']
        stats['rate'] = round(rate, 4)
        mobility_rows.append(stats)

    village_rates = [row['rate'] for row in village_rows]
    mobility_rates = {row['mobility']: row['rate'] for row in mobility_rows}
    mobility_gap = (max(mobility_rates.values()) - min(mobility_rates.values())
                    if mobility_rates else 0.0)
    offer_rows = []
    for offer in offers:
        used = offer_flows.get(offer['offer'], 0)
        offer_rows.append(dict(offer=offer['offer'], site=offer['site'],
                               topic=offer['topic'], slot=offer['slot'],
                               seats=offer['seats'], used=used,
                               residual=offer['seats'] - used,
                               source=offer['source'], proposal=offer['proposal']))
    total_population = sum(populations.values())
    total_seats = sum(offer['seats'] for offer in offers)
    preference_achieved = -flow_cost / WEIGHT_SCALE
    preference_coverage = (preference_achieved / preference_potential
                           if preference_potential else 0.0)
    return {
        'scenario': {'proposals': list(proposal_ids), 'travel': travel_level,
                     'population': population_level},
        'population_total': total_population,
        'served_total': served_flow,
        'unserved_total': total_population - served_flow,
        'seats_total': total_seats,
        'seats_used': sum(row['used'] for row in offer_rows),
        'preference_weight_achieved': round(preference_achieved, 3),
        'preference_weight_potential': round(preference_potential, 3),
        'preference_coverage': round(preference_coverage, 4),
        'mobility': mobility_rows,
        'villages': village_rows,
        'disparity': {
            'village_rate_min': round(min(village_rates), 4) if village_rates else 0.0,
            'village_rate_max': round(max(village_rates), 4) if village_rates else 0.0,
            'village_rate_gap': round((max(village_rates) - min(village_rates)), 4)
            if village_rates else 0.0,
            'mobility_rate_gap': round(mobility_gap, 4),
        },
        'groups': group_rows,
        'offers': offer_rows,
        'reasons': [dict(reason=reason, label=REASON_LABELS[reason],
                         groups=sorted(payload['groups']),
                         population=payload['population'])
                    for reason, payload in sorted(reason_population.items())],
        'meaning': 'capacity_bounded_service_potential_not_enrollment_assignment',
    }


def _scenario_grid(store, proposal_ids):
    cells = {}
    for travel in TRAVEL_LEVELS:
        for population in POPULATION_LEVELS:
            cells[travel + '|' + population] = evaluate_scenario(
                store, proposal_ids, travel, population)
    served = [cell['served_total'] for cell in cells.values()]
    unserved = [cell['unserved_total'] for cell in cells.values()]
    return {
        'proposals': list(proposal_ids),
        'cells': cells,
        'served_range': [min(served), max(served)],
        'unserved_range': [min(unserved), max(unserved)],
        'base': cells['base|base'],
    }


def _group_usage_on_offer(store, proposal_ids, group_id, offer_id):
    """仅含该组的二部网络偏好最优解中，指定班级承接的流量。

    用于区分“新增服务”与“本来就能上课的人换到新班”：
    当组级总服务量没有增加、但新班确实承接了流量时即为后者。
    """
    group = store.find('groups', group=group_id)
    catalog = eligibility_catalog(store, 'base', tuple(proposal_ids))
    entries = catalog[group_id]['eligible']
    if not any(entry['offer'] == offer_id for entry in entries):
        return 0
    network = FlowNetwork()
    population = group['population']
    network.add_edge(SOURCE, group_id, population)
    for entry in entries:
        network.add_edge(entry['offer'], SINK, 10 ** 9)
        cost = -round(entry['preference_weight'] * WEIGHT_SCALE)
        network.add_edge(group_id, entry['offer'], population, cost=cost)
    network.min_cost_max_flow(SOURCE, SINK)
    return network.edge_flows(group_id).get(offer_id, 0)


def compare_proposals(store, proposal_ids=None):
    """现状与各方案在调查区间上的稳健比较。"""
    if proposal_ids is None:
        proposal_ids = [row['proposal'] for row in store.table('proposals')]
    proposal_ids = list(proposal_ids)
    if not proposal_ids:
        raise DataError('at least one proposal required for comparison')
    for proposal_id in proposal_ids:
        store.find('proposals', proposal=proposal_id)
    if len(set(proposal_ids)) != len(proposal_ids):
        raise DataError('duplicate proposal in comparison scope')

    scenario_ids = ['baseline', *proposal_ids, 'combined']
    scenario_proposals = {'baseline': (), 'combined': tuple(proposal_ids)}
    for proposal_id in proposal_ids:
        scenario_proposals[proposal_id] = (proposal_id,)

    scenarios = {}
    for scenario_id in scenario_ids:
        scenarios[scenario_id] = _scenario_grid(
            store, scenario_proposals[scenario_id])

    # 9 个区间格点上的逐格排名，检验排序是否随合理取值翻转。
    cell_rankings = []
    for travel in TRAVEL_LEVELS:
        for population in POPULATION_LEVELS:
            key = travel + '|' + population
            ordering = sorted(
                scenario_ids,
                key=lambda sid: (-scenarios[sid]['cells'][key]['served_total'], sid))
            cell_rankings.append({'travel': travel, 'population': population,
                                  'ordering': ordering})
    robust_ordering = cell_rankings[0]['ordering']
    robust = all(item['ordering'] == robust_ordering for item in cell_rankings)
    median_order = sorted(
        scenario_ids,
        key=lambda sid: (-sorted(scenarios[sid]['cells'][key]['served_total']
                                 for key in scenarios[sid]['cells'])[4], sid))

    pairwise = []
    for index, left in enumerate(scenario_ids):
        for right in scenario_ids[index + 1:]:
            # 差值统一为 b - a：正数表示 b 的可服务人数更多/地区差距改善更多。
            served_cells, gap_cells = [], []
            for travel in TRAVEL_LEVELS:
                for population in POPULATION_LEVELS:
                    key = travel + '|' + population
                    ls = scenarios[left]['cells'][key]
                    rs = scenarios[right]['cells'][key]
                    served_cells.append(rs['served_total']
                                        - ls['served_total'])
                    gap_cells.append(ls['disparity']['village_rate_gap']
                                     - rs['disparity']['village_rate_gap'])
            pairwise.append({
                'a': left, 'b': right,
                'metric': 'served_total_delta_b_minus_a',
                'served_delta_cells': served_cells,
                'served_wins_a': sum(1 for value in served_cells if value < 0),
                'served_wins_b': sum(1 for value in served_cells if value > 0),
                'served_ties': sum(1 for value in served_cells if value == 0),
                'served_ordering_flips': any(v > 0 for v in served_cells)
                and any(v < 0 for v in served_cells),
                'gap_improvement_cells_for_a': gap_cells,
                'gap_wins_a': sum(1 for value in gap_cells if value > 0),
                'gap_wins_b': sum(1 for value in gap_cells if value < 0),
                'gap_ordering_flips': any(v > 0 for v in gap_cells)
                and any(v < 0 for v in gap_cells),
            })

    # 受影响人群：以基准情景为对照的组级新增服务，保留低/高区间。
    affected = {}
    substitution = {}
    baseline_base = scenarios['baseline']['base']
    for proposal_id in proposal_ids:
        proposal_base = scenarios[proposal_id]['base']
        new_offer = 'proposal:' + proposal_id
        group_gains, substitution_groups = [], []
        for before in baseline_base['groups']:
            after = next(row for row in proposal_base['groups']
                         if row['group'] == before['group'])
            gain_cells = []
            for travel in TRAVEL_LEVELS:
                for population in POPULATION_LEVELS:
                    key = travel + '|' + population
                    b = scenarios['baseline']['cells'][key]
                    p = scenarios[proposal_id]['cells'][key]
                    b_group = next(row for row in b['groups']
                                   if row['group'] == before['group'])
                    p_group = next(row for row in p['groups']
                                   if row['group'] == before['group'])
                    gain_cells.append(p_group['served'] - b_group['served'])
            gain_base = after['served'] - before['served']
            if gain_base > 0:
                group_gains.append(dict(
                    group=before['group'], village=before['village'],
                    mobility=before['mobility'], gain_base=gain_base,
                    gain_low=min(gain_cells), gain_high=max(gain_cells)))
            elif gain_base == 0:
                # 该组在新班承接了流量但总服务量未增：换地方而非新增服务。
                redirected = _group_usage_on_offer(
                    store, (proposal_id,), before['group'], new_offer)
                if redirected > 0:
                    substitution_groups.append(dict(
                        group=before['group'], village=before['village'],
                        seats_redirected=redirected))
        affected[proposal_id] = {
            'groups': group_gains,
            'population_gain_base': sum(item['gain_base'] for item in group_gains),
            'population_gain_range': [
                sum(item['gain_low'] for item in group_gains),
                sum(item['gain_high'] for item in group_gains)],
        }
        substitution[proposal_id] = {
            'groups': substitution_groups,
            'seats_redirected_base': sum(item['seats_redirected']
                                         for item in substitution_groups),
        }

    # 重叠覆盖：多方案共同新增的同一批人只能在合并情景里计一次。
    covered_sets = {pid: {item['group'] for item in affected[pid]['groups']}
                    for pid in proposal_ids}
    shared = set.intersection(*covered_sets.values()) if covered_sets else set()
    gross_gain = sum(affected[pid]['population_gain_base'] for pid in proposal_ids)
    combined_gain = (scenarios['combined']['base']['served_total']
                     - baseline_base['served_total'])
    gross_cells, combined_cells = [], []
    for travel in TRAVEL_LEVELS:
        for population in POPULATION_LEVELS:
            key = travel + '|' + population
            gross_cells.append(sum(
                scenarios[pid]['cells'][key]['served_total']
                - scenarios['baseline']['cells'][key]['served_total']
                for pid in proposal_ids))
            combined_cells.append(
                scenarios['combined']['cells'][key]['served_total']
                - scenarios['baseline']['cells'][key]['served_total'])
    double_cells = [gross - net for gross, net in zip(gross_cells, combined_cells)]

    return {
        'dataset_fingerprint': store.fingerprint(),
        'proposals': proposal_ids,
        'scenarios': scenarios,
        'ranking': {
            'by_served_median': median_order,
            'robust_in_all_cells': robust,
            'cell_rankings': cell_rankings,
            'note': 'interval_comparison_not_a_fixed_ranking',
        },
        'pairwise': pairwise,
        'affected': affected,
        'substitution': substitution,
        'overlap': {
            'shared_affected_groups': sorted(shared),
            'gross_gain_base': gross_gain,
            'combined_gain_base': combined_gain,
            'double_count_base': gross_gain - combined_gain,
            'double_count_cells': double_cells,
            'double_count_range': [min(double_cells), max(double_cells)],
        },
        'meaning': 'service_potential_under_shared_seats_no_enrollment_assignment',
    }
