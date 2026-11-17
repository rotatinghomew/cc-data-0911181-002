from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from app.analysis import ROUTES
from app.reporting import report
from app.service import dispatch, make_server
from app.storage import DataError, Store, integer, single

DATA = Path(__file__).parents[1] / 'data'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)

    def test_every_table_has_data(self):
        self.assertTrue(all(count > 0 for count in self.store.inventory().values()))

    def test_isolated_reads(self):
        name = next(iter(self.store.schema))
        rows = self.store.table(name)
        rows[0]['accidental_edit'] = True
        self.assertNotIn('accidental_edit', self.store.table(name)[0])

    def test_stable_fingerprint(self):
        self.assertEqual(self.store.fingerprint(), Store(DATA).fingerprint())
        self.assertEqual(len(self.store.fingerprint()), 64)

    def test_duplicate_primary_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data'
            shutil.copytree(DATA, target)
            name = next(iter(self.store.schema))
            path = target / (name + '.json')
            rows = json.loads(path.read_text())
            rows.append(deepcopy(rows[0]))
            path.write_text(json.dumps(rows))
            with self.assertRaises(DataError):
                Store(target)

    def test_missing_required_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data'
            shutil.copytree(DATA, target)
            name, contract = next(iter(self.store.schema.items()))
            path = target / (name + '.json')
            rows = json.loads(path.read_text())
            del rows[0][contract['required'][0]]
            path.write_text(json.dumps(rows))
            with self.assertRaises(DataError):
                Store(target)

    def test_query_boundaries(self):
        self.assertEqual(integer({}, 'limit', 5), 5)
        for query in [{'limit': ['x']}, {'limit': ['-1']}, {'limit': ['1', '2']}]:
            with self.assertRaises(DataError):
                integer(query, 'limit', 5)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(DATA)
        cls.server = make_server(cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_health_and_routes_over_http(self):
        for path in ['/health', *ROUTES]:
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('application/json', response.headers['Content-Type'])
                self.assertIsInstance(json.load(response), dict)

    def test_unknown_route(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/does-not-exist', timeout=3)
        self.assertEqual(caught.exception.code, 404)

    def test_bad_table(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/records?table=unknown', timeout=3)
        self.assertEqual(caught.exception.code, 400)

    def test_report_uses_same_analysis(self):
        result = report(self.store)
        self.assertEqual(result['dataset_fingerprint'], self.store.fingerprint())
        for path in ROUTES:
            self.assertEqual(result['sections'][path], dispatch(self.store, path, {}))


from app.domain import eligible_offers, nearest_site


class ReachTests(unittest.TestCase):
    def test_minimum_route(self):
        store = Store(DATA)
        group = store.table('groups')[0]
        nearest = nearest_site(store, group)
        self.assertEqual(nearest['minutes'], min(row['minutes'] for row in
                         store.select('routes', group=group['group'])))

    def test_reach_obeys_slot_and_mobility(self):
        store = Store(DATA)
        for group in store.table('groups'):
            for offer_id in eligible_offers(store, group):
                offer = store.find('offers', offer=offer_id)
                route = store.find('routes', group=group['group'], site=offer['site'])
                self.assertIn(offer['slot'], group['slots'])
                self.assertLessEqual(route['minutes'], group['maximum_minutes'])

    def test_village_filter_preserves_capacity_catalog(self):
        store = Store(DATA)
        rows = dispatch(store, '/api/reach', {'village':['V0']})['groups']
        self.assertEqual(len(rows), 2)
        self.assertEqual(report(store)['sections']['/api/capacity']['seats'], 150)
