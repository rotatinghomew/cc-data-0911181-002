from pathlib import Path
import unittest

from app.accessibility import (POPULATION_LEVELS, TRAVEL_LEVELS,
                                compare_proposals, eligibility_catalog,
                                evaluate_scenario)
from app.flow import FlowNetwork
from app.service import dispatch, make_server
from app.storage import DataError, Store
import json
import threading
from urllib.request import urlopen

DATA = Path(__file__).parents[1] / 'data'


class FlowNetworkTests(unittest.TestCase):
    def test_max_flow_respects_shared_seats(self):
        network = FlowNetwork()
        network.add_edge('source', 'P0', 30)
        network.add_edge('source', 'P1', 30)
        network.add_edge('P0', 'C0', 30)
        network.add_edge('P1', 'C0', 30)
        network.add_edge('C0', 'sink', 25)
        self.assertEqual(network.max_flow('source', 'sink'), 25)

    def test_min_cost_prefers_cheaper_path_at_max_flow(self):
        network = FlowNetwork()
        network.add_edge('source', 'P', 10)
        network.add_edge('liked', 'sink', 10, cost=0)
        network.add_edge('other', 'sink', 10, cost=0)
        network.add_edge('P', 'liked', 10, cost=-5)
        network.add_edge('P', 'other', 10, cost=-1)
        flow, cost = network.min_cost_max_flow('source', 'sink')
        self.assertEqual(flow, 10)
        self.assertEqual(cost, -50)
        self.assertEqual(network.edge_flows('P')['liked'], 10)


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)

    def test_travel_threshold_uses_mobility_limit(self):
        # P3 为 limited（上限 15 分钟），高区间下 S3 的 14 分钟变成 19 分钟，
        # 该组将没有任何可达学校。
        low = eligibility_catalog(self.store, 'low')
        high = eligibility_catalog(self.store, 'high')
        self.assertTrue(low['P3']['reachable_sites'])
        self.assertEqual(high['P3']['reachable_sites'], [])

    def test_proposal_offers_only_appear_when_requested(self):
        baseline = eligibility_catalog(self.store)
        proposal = eligibility_catalog(self.store, 'base', ('nearby',))
        baseline_offers = {offer for entry in baseline.values()
                           for offer in entry['reachable_offers']}
        proposal_offers = {offer for entry in proposal.values()
                           for offer in entry['reachable_offers']}
        self.assertNotIn('proposal:nearby', baseline_offers)
        self.assertIn('proposal:nearby', proposal_offers)

    def test_slot_mismatch_is_distinct_from_reachability(self):
        # P5 能到达 S2，但只参加 morning，而 S2 只有 afternoon/evening 班。
        entry = eligibility_catalog(self.store)['P5']
        self.assertTrue(entry['reachable_sites'])
        self.assertTrue(entry['reachable_offers'])
        self.assertEqual(entry['slot_matching_offers'], [])

    def test_invalid_level_rejected(self):
        with self.assertRaises(DataError):
            evaluate_scenario(self.store, (), travel_level='middle')
        with self.assertRaises(DataError):
            evaluate_scenario(self.store, (), population_level='middle')

    def test_unknown_proposal_rejected(self):
        with self.assertRaises(DataError):
            compare_proposals(self.store, ['nope'])


class ScenarioEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)

    def test_baseline_caps_at_available_seats(self):
        result = evaluate_scenario(self.store)
        self.assertEqual(result['seats_total'], 150)
        self.assertEqual(result['served_total'], 150)
        self.assertLess(result['served_total'], result['population_total'])
        self.assertEqual(sum(row['used'] for row in result['offers']),
                         result['served_total'])
        for row in result['offers']:
            self.assertLessEqual(row['used'], row['seats'])
            self.assertGreaterEqual(row['residual'], 0)

    def test_unserved_reasons_are_exclusive_and_complete(self):
        result = evaluate_scenario(self.store)
        grouped = {}
        for reason in result['reasons']:
            for gid in reason['groups']:
                self.assertNotIn(gid, grouped)
                grouped[gid] = reason['reason']
        for group in result['groups']:
            self.assertEqual(grouped[group['group']], group['reason'])
        reason_population = sum(reason['population'] for reason in result['reasons']
                                if reason['reason'] != 'fully_served')
        self.assertEqual(reason_population, result['unserved_total'])

    def test_groups_are_counted_once_under_overlapping_coverage(self):
        baseline = evaluate_scenario(self.store)
        combined = evaluate_scenario(self.store, ('nearby', 'remote'))
        # 合并情景的服务量不超过各组人口之和，且每个组只出现一次。
        self.assertEqual(len(combined['groups']), len(baseline['groups']))
        self.assertLessEqual(
            combined['served_total'],
            sum(group['population'] for group in combined['groups']))
        self.assertGreater(combined['served_total'], baseline['served_total'])

    def test_proposal_seats_that_only_redirect_are_not_new_service(self):
        comparison = compare_proposals(self.store)
        # nearby 方案在 P4/P8/P10 上承接流量但不增加这些组的服务量，
        # 必须归入“换地方”，而不是新增服务人口。
        nearby = comparison['substitution']['nearby']
        self.assertIn('P4', [item['group'] for item in nearby['groups']])
        self.assertGreater(nearby['seats_redirected_base'], 0)
        affected_groups = {item['group']
                           for item in comparison['affected']['nearby']['groups']}
        self.assertTrue(affected_groups.isdisjoint(
            item['group'] for item in nearby['groups']))

    def test_disparity_reports_village_and_mobility_gap(self):
        result = evaluate_scenario(self.store)
        rates = [village['rate'] for village in result['villages']]
        self.assertEqual(result['disparity']['village_rate_gap'],
                         round(max(rates) - min(rates), 4))
        self.assertGreaterEqual(result['disparity']['mobility_rate_gap'], 0)
        limited = next(row for row in result['mobility']
                       if row['mobility'] == 'limited')
        ordinary = next(row for row in result['mobility']
                        if row['mobility'] == 'ordinary')
        self.assertLess(limited['rate'], ordinary['rate'])

    def test_preference_is_retained_as_weight_not_assignment(self):
        result = evaluate_scenario(self.store)
        self.assertGreater(result['preference_weight_achieved'], 0)
        self.assertLessEqual(result['preference_coverage'], 1)
        self.assertEqual(result['meaning'],
                         'capacity_bounded_service_potential_not_enrollment_assignment')


class UncertaintyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)
        self.comparison = compare_proposals(self.store)

    def test_nine_cells_cover_survey_intervals(self):
        for scenario in self.comparison['scenarios'].values():
            self.assertEqual(len(scenario['cells']), 9)
            low, high = scenario['served_range']
            self.assertLessEqual(low, high)

    def test_high_travel_reduces_or_keeps_service(self):
        cells = self.comparison['scenarios']['baseline']['cells']
        for population in POPULATION_LEVELS:
            low = cells['low|' + population]['served_total']
            high = cells['high|' + population]['served_total']
            self.assertGreaterEqual(low, high)

    def test_group_gain_carries_low_high_interval(self):
        for proposal_id in self.comparison['proposals']:
            affected = self.comparison['affected'][proposal_id]
            for group in affected['groups']:
                self.assertLessEqual(group['gain_low'], group['gain_base'])
                self.assertGreaterEqual(group['gain_high'], group['gain_base'])
            low, high = affected['population_gain_range']
            self.assertEqual(low, sum(item['gain_low']
                                      for item in affected['groups']))
            self.assertEqual(high, sum(item['gain_high']
                                       for item in affected['groups']))

    def test_overlap_prevents_double_counting(self):
        overlap = self.comparison['overlap']
        baseline = self.comparison['scenarios']['baseline']['base']['served_total']
        gross = overlap['gross_gain_base']
        for cell in overlap['double_count_cells']:
            self.assertGreaterEqual(cell, 0)
        self.assertEqual(
            overlap['combined_gain_base'],
            self.comparison['scenarios']['combined']['base']['served_total']
            - baseline)
        self.assertEqual(overlap['double_count_base'],
                         gross - overlap['combined_gain_base'])

    def test_ranking_reports_robustness_without_false_precision(self):
        ranking = self.comparison['ranking']
        self.assertEqual(len(ranking['cell_rankings']), 9)
        self.assertIn(ranking['note'],
                      'interval_comparison_not_a_fixed_ranking')
        for cell in ranking['cell_rankings']:
            self.assertEqual(cell['ordering'][0], 'combined')
            self.assertEqual(cell['ordering'][-1], 'baseline')

    def test_synthetic_flip_is_surfaced_not_hidden(self):
        # 合成会翻转排序的数据：高出行区间下 S0 对所有组不可达，
        # nearby（设在 S0）新增归零，而 remote（S3）仍有新增；
        # 低/基准区间 nearby 因座位更多而领先。比较结果必须暴露翻转，
        # 而不是输出一个看似精确的固定排名。
        from copy import deepcopy
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data'
            shutil.copytree(DATA, target)
            routes = json.loads((target / 'routes.json').read_text())
            for row in routes:
                if row['site'] == 'S0':
                    row['minutes_high'] = 99
            (target / 'routes.json').write_text(
                json.dumps(routes, ensure_ascii=False))
            synthetic = Store(target)
            result = compare_proposals(synthetic)
            pair = next(item for item in result['pairwise']
                        if {item['a'], item['b']} == {'nearby', 'remote'})
            self.assertTrue(pair['served_ordering_flips'])
            self.assertFalse(result['ranking']['robust_in_all_cells'])
            self.assertTrue(any(cell['ordering'][1] == 'nearby'
                                for cell in result['ranking']['cell_rankings']))
            self.assertTrue(any(cell['ordering'][1] == 'remote'
                                for cell in result['ranking']['cell_rankings']))


class HttpAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(DATA)
        cls.server = make_server(cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_accessibility_endpoint_filters(self):
        with urlopen(self.base + '/api/accessibility', timeout=5) as response:
            payload = json.load(response)
        self.assertEqual(payload['scenario']['proposals'], [])
        with urlopen(self.base
                     + '/api/accessibility?proposals=nearby&travel=high',
                     timeout=5) as response:
            payload = json.load(response)
        self.assertEqual(payload['scenario']['proposals'], ['nearby'])
        self.assertEqual(payload['scenario']['travel'], 'high')

    def test_comparison_endpoint_and_bad_input(self):
        from urllib.error import HTTPError
        with urlopen(self.base + '/api/proposal-comparison', timeout=10) as response:
            payload = json.load(response)
        self.assertEqual(set(payload['proposals']), {'nearby', 'remote'})
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/accessibility?travel=middle', timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_dispatch_report_includes_new_routes(self):
        from app.analysis import ROUTES
        self.assertIn('/api/accessibility', ROUTES)
        self.assertIn('/api/proposal-comparison', ROUTES)
        payload = dispatch(self.store, '/api/accessibility', {})
        self.assertEqual(payload['served_total'], 150)

    def test_bad_population_and_unknown_basis_return_400(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/accessibility?population=middle',
                    timeout=5)
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/comparison-basis?basis=missing',
                    timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_proposal_filter_accepts_csv_list(self):
        with urlopen(self.base + '/api/proposal-comparison?proposals=remote',
                     timeout=10) as response:
            payload = json.load(response)
        self.assertEqual(payload['proposals'], ['remote'])
        self.assertEqual(sorted(payload['scenarios']),
                         ['baseline', 'combined', 'remote'])


if __name__ == '__main__':
    unittest.main()
